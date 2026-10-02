"""Share / backup: one all-inclusive zip of an OmaSkins setup, and importing one (batch 5).

The zip holds a manifest, omaskins.json (FORMAT), that lists everything in it by group, so the other
OmaSkins knows what's there before unpacking anything:
- themes: Omarchy's built-ins by name; omarchy.org-listed themes as their link (installed the same
  way Omarchy's own installer does); everything else (Aether-made, unlisted, or a listed theme you
  edited) as the whole folder;
- backgrounds you added yourself, as the files;
- fonts: Arch packages by name; fonts not in the repos (AUR, self-built) or from no package, as files;
- the rotation: the saved set in use by name, or (no set, or unsaved changes) the whole setup;
  every saved set; background choices as theme + file name, never full paths;
- the look: current theme, background and font, Rounded Corners, Transparency, text size, built-ins hidden
  or removed, Aether's blueprints, the sort order. Not the location: Dawn & Dusk works that out on
  each machine (when it's switched on, then once per boot).

Importing merges whenever it can and never overwrites anything you have (owner, 2026-10-01: "MERGE
ANYTIME ITS POSSIBLE"): a theme whose name matches one of yours, exactly or nearly (Oil Paintings /
oil_paintings / oil-paintings), adds the pictures you don't have yet to yours (compared by content)
and keeps your colours and settings; a background already here is skipped; a Rotation set with the
name of one of yours is combined with it. Theme folders from a zip lose the files that run code, as
Omarchy does for themes downloaded from git (INSTALLED_THEME_DENIED).
"""

import atexit
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from . import data, qtstyle

FORMAT = "omaskins-share/2"
EXT = ".omaskins"   # a zip inside; its own file type (MIME_TYPE) so file managers don't unpack it on open
MIME_TYPE = "application/x-omaskins-setup"
MANIFEST = "omaskins.json"
MAX_MEMBERS = 20000
MAX_UNPACKED = 4 * 1024 ** 3          # 4 GB in all
MAX_MEMBER = 600 * 1024 ** 2          # 600 MB for one file
# Omarchy's own list (omarchy-theme-set INSTALLED_THEME_DENIED) plus every .lua: these run code.
DENIED_NAMES = {"alacritty.toml", "foot.ini", "ghostty.conf", "kitty.conf", "vscode.json"}
FONT_EXT = (".ttf", ".otf", ".ttc", ".woff", ".woff2", ".pcf", ".pcf.gz", ".bdf")
IMPORTED_FONTS = data.HOME / ".local/share/fonts/omaskins-imported"


class BadZip(Exception):
    pass


# Nothing half-made is ever left behind: files being written are tracked and removed if the write
# fails, or if OmaSkins quits in the middle of one.
_IN_PROGRESS = set()


def _forget_unfinished():
    for path in list(_IN_PROGRESS):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    _IN_PROGRESS.clear()


atexit.register(_forget_unfinished)


class _unfinished:
    """with _unfinished(path): … - the path is removed unless the block completes."""

    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        _IN_PROGRESS.add(self.path)
        return self.path

    def __exit__(self, kind, *_):
        _IN_PROGRESS.discard(self.path)
        if kind is not None:
            if self.path.is_dir() and not self.path.is_symlink():
                shutil.rmtree(self.path, ignore_errors=True)
            else:
                self.path.unlink(missing_ok=True)
        return False


def _write_new(dest, payload):
    """A new file, complete or not at all (written beside it, then renamed in)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f".{dest.name}.part")
    with _unfinished(part):
        part.write_bytes(payload)
        os.replace(part, dest)


def denied(name):
    return name.endswith(".lua") or name in DENIED_NAMES


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- background references
#
# A background is named by where it lives, not by its path (which has your user name in it):
# {"theme": name, "file": name} for a theme's own picture, plus "own": true for one you added
# (~/.config/omarchy/backgrounds/<theme>/).

def ref_of(path):
    p = Path(os.path.realpath(path))
    for root, own in ((data.USER_BACKGROUNDS, True), (data.USER_THEMES, False), (data.BUILTIN_THEMES, False)):
        try:
            rel = p.relative_to(os.path.realpath(root))
        except ValueError:
            continue
        parts = rel.parts
        if own and len(parts) == 2:
            return {"theme": parts[0], "file": parts[1], "own": True}
        if not own and len(parts) == 3 and parts[1] == "backgrounds":
            return {"theme": parts[0], "file": parts[2]}
    return None


def path_of(ref):
    """The file a reference means on this machine, or None."""
    if not isinstance(ref, dict) or not ref.get("theme") or not ref.get("file"):
        return None
    theme, name = str(ref["theme"]), str(ref["file"])
    if "/" in theme or "/" in name or theme.startswith(".") or name.startswith("."):
        return None
    if ref.get("own"):
        p = data.USER_BACKGROUNDS / theme / name
        return p if p.is_file() else None
    for root in (data.USER_THEMES, data.BUILTIN_THEMES):
        p = root / theme / "backgrounds" / name
        if p.is_file():
            return p
    return None


def portable(setup):
    """A Rotation setup (RotationPlan.to_dict) with background paths turned into references."""
    out = dict(setup)
    out["theme_picks"] = {t: [r for r in (ref_of(p) for p in ps) if r]
                          for t, ps in (setup.get("theme_picks") or {}).items()}
    if setup.get("solo_picks") is not None:
        out["solo_picks"] = [r for r in (ref_of(p) for p in setup["solo_picks"]) if r]
    return out


def local(setup):
    """The other way: references back into paths on this machine (pictures missing here are dropped)."""
    out = dict(setup)
    out["theme_picks"] = {t: [str(p) for p in (path_of(r) for r in refs) if p]
                          for t, refs in (setup.get("theme_picks") or {}).items() if isinstance(refs, list)}
    if isinstance(setup.get("solo_picks"), list):
        out["solo_picks"] = [str(p) for p in (path_of(r) for r in setup["solo_picks"]) if p]
    return out


# --------------------------------------------------------------------------- the file type

MIME_PACKAGE = data.HOME / ".local/share/mime/packages/io.github.jesuslovesyou1013.omaskins.xml"
MIME_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="{MIME_TYPE}">
    <comment>OmaSkins setup</comment>
    <glob pattern="*{EXT}" weight="100"/>
    <generic-icon name="preferences-desktop-theme"/>
  </mime-type>
</mime-info>
"""


APP_DESKTOP = data.HOME / ".local/share/applications/io.github.jesuslovesyou1013.omaskins.desktop"
MIMEAPPS = Path(os.environ.get("XDG_CONFIG_HOME") or data.HOME / ".config") / "mimeapps.list"


def _desktop_entry():
    """Hidden (NoDisplay: no second OmaSkins in the app launcher); only says OmaSkins opens its setups."""
    launcher = Path(__file__).resolve().parent.parent / "omaskins-manager"
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=OmaSkins Manager\n"
            "Comment=Import an OmaSkins setup\n"
            f'Exec="{launcher}" %f\n'
            "Icon=preferences-desktop-theme\n"
            f"MimeType={MIME_TYPE};\n"
            "NoDisplay=true\n"
            "Terminal=false\n")


def register_file_type():
    """Tell the desktop that *.omaskins is an OmaSkins setup, opened by OmaSkins. Yours only
    (~/.local/share, ~/.config/mimeapps.list), no password, and nothing but that one file type:
    - its type (MIME_PACKAGE): without it a file manager looks inside, sees a zip, and unpacks it next
      to the file when you open it (Nautilus does, with no archive app installed);
    - a hidden launcher entry (APP_DESKTOP) for opening it;
    - OmaSkins as the app for it, unless you've already picked another one for it."""
    try:
        if not (MIME_PACKAGE.exists() and MIME_PACKAGE.read_text() == MIME_XML):
            _write_new(MIME_PACKAGE, MIME_XML.encode())
            subprocess.run(["update-mime-database", str(MIME_PACKAGE.parent.parent)], capture_output=True,
                           timeout=60, env=data.child_env())
        entry = _desktop_entry()
        if not (APP_DESKTOP.exists() and APP_DESKTOP.read_text() == entry):
            _write_new(APP_DESKTOP, entry.encode())
        import gi
        try:
            gi.require_version("GioUnix", "2.0")
            from gi.repository import GioUnix as Gio
        except (ValueError, ImportError):
            from gi.repository import Gio
        chosen = MIMEAPPS.read_text() if MIMEAPPS.exists() else ""
        if not re.search(rf"^{re.escape(MIME_TYPE)}=", chosen, re.M):   # nothing chosen for it yet
            app = Gio.DesktopAppInfo.new_from_filename(str(APP_DESKTOP))
            if app:
                app.set_as_default_for_type(MIME_TYPE)
    except (OSError, subprocess.SubprocessError, ImportError, ValueError):
        pass
    except Exception:  # noqa: BLE001 (GLib.Error: never let this get in the way of the window)
        pass


# --------------------------------------------------------------------------- export

def _git(path, *args):
    try:
        return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=20,
                              env=data.child_env())
    except (OSError, subprocess.SubprocessError):
        return None


def _theme_entry(t, listed_by_name):
    """One theme for the manifest; (entry, files to copy into the zip)."""
    if t.builtin:
        return {"name": t.name, "kind": "builtin"}, []
    listed = listed_by_name.get(t.name)
    if listed and (t.path / ".git").is_dir():
        status = _git(t.path, "status", "--porcelain")
        head = _git(t.path, "rev-parse", "HEAD")
        if status is not None and status.returncode == 0 and not status.stdout.strip():
            return {"name": t.name, "kind": "link", "repo": listed.repo_url,
                    "commit": head.stdout.strip() if head and head.returncode == 0 else ""}, []
    files = []
    for p in sorted(t.path.rglob("*")):
        rel = p.relative_to(t.path)
        if p.is_file() and not p.is_symlink() and ".git" not in rel.parts:
            files.append((p, f"themes/{t.name}/{rel.as_posix()}"))
    kind = "aether" if t.aether else "edited" if listed else "folder"
    return {"name": t.name, "kind": kind, "files": len(files),
            "bytes": sum(p.stat().st_size for p, _ in files)}, files


def _font_entries():
    """Every installed monospace family: by package when the repos have it, else its files."""
    out, files, in_repo = [], [], {}
    for f in data.installed_fonts():
        entry = {"family": f.family, "package": f.package or "", "current": bool(f.current)}
        if f.package:
            if f.package not in in_repo:
                r = subprocess.run(["pacman", "-Si", f.package], capture_output=True, env=data.child_env())
                in_repo[f.package] = r.returncode == 0
            entry["source"] = "repo" if in_repo[f.package] else "other"
        else:
            entry["source"] = "none"
        if entry["source"] != "repo":
            listed = data._run(["fc-list", "-f", "%{file}\n", f":family={f.family}"]).splitlines()
            entry["files"] = []
            for path in sorted(set(listed)):
                p = Path(path)
                if p.is_file() and p.name.lower().endswith(FONT_EXT):
                    name = f"fonts/{p.name}"
                    files.append((p, name))
                    entry["files"].append(name)
        out.append(entry)
    return out, files


def collect(community):
    """(manifest, [(file on disk, path in the zip)]) for everything OmaSkins manages."""
    files = []
    local_themes = data.local_themes()
    listed = data.match_installed(community, local_themes)
    listed_by_name = {t.name: c for c in community if (t := listed.get(c.key))}
    themes = []
    for t in local_themes:
        entry, theme_files = _theme_entry(t, listed_by_name)
        themes.append(entry)
        files += theme_files

    backgrounds = []
    for p in sorted(data.USER_BACKGROUNDS.glob("*/*")):
        if p.is_file() and p.suffix.lower() in data.IMAGE_EXT:
            zname = f"backgrounds/{p.parent.name}/{p.name}"
            files.append((p, zname))
            backgrounds.append({"theme": p.parent.name, "file": p.name, "zip": zname, "bytes": p.stat().st_size})

    fonts, font_files = _font_entries()
    files += font_files

    current_bg = data.current_background()
    current = {"theme": data.current_theme_name(), "font": data.current_font()}
    if current_bg:
        ref = ref_of(current_bg)
        if ref:
            current["background"] = ref
        elif current_bg.is_file():
            files.append((current_bg, f"current/{current_bg.name}"))
            current["background"] = {"zip": f"current/{current_bg.name}"}

    plan = data.load_rotation(current["theme"])
    if plan.set_name and not data.set_has_changes(plan):
        rotation = {"set": plan.set_name}
    else:
        rotation = {"settings": portable(plan.to_dict())}
    sets = {name: portable(setup) for name, setup in data.rotation_sets().items()}

    blueprints = []
    for p in sorted(data.AETHER_BLUEPRINTS.glob("*.json")):
        files.append((p, f"aether-blueprints/{p.name}"))
        blueprints.append(f"aether-blueprints/{p.name}")

    on, px = data.corners_setting()
    builtin_state = data._builtin_state()
    text_size = None
    try:
        text_size = json.loads(data.TEXT_SIZE_STATE.read_text()).get("base")
    except (OSError, ValueError, AttributeError):
        pass

    manifest = {
        "format": FORMAT,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "themes": themes,
        # Hidden from OmaSkins only (still installed there) and removed entirely (gone from Omarchy's menu
        # too), apart, so an import can offer to do the same of each (owner, 2026-10-02).
        "hidden_builtins": sorted(builtin_state.get("hidden", [])),
        "removed_builtins": data.removed_builtins(data.shipped_builtins(data.builtin_package())),
        "backgrounds": backgrounds,
        "fonts": fonts,
        "current": current,
        "corners": {"on": on, "px": px},
        "transparency": data.transparency_step(),
        "qt_apps": qtstyle.enabled(),
        "text_size": text_size,
        "rotation": rotation,
        "rotation_sets": sets,
        "aether_blueprints": blueprints,
        "sort": data.sort_mode(),
    }
    return manifest, files


def write_zip(dest, community, progress=None, collected=None):
    """Write the zip (atomically: a .part file renamed when complete). Returns (manifest, bytes).
    collected = what collect() already returned (the Share window shows it first)."""
    manifest, files = collected or collect(community)
    dest = Path(dest)
    part = dest.with_name(f".{dest.name}.part")
    total = sum(p.stat().st_size for p, _ in files) or 1
    done = 0
    with _unfinished(part), zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps(manifest, indent=1))
        for p, name in files:
            # Pictures and fonts are already compressed: store them, deflate the rest.
            stored = p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".ttf", ".otf", ".woff2")
            z.write(p, name, compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
            done += p.stat().st_size
            if progress:
                progress(int(done * 100 / total))
        z.close()
        os.replace(part, dest)
    return manifest, dest.stat().st_size


# --------------------------------------------------------------------------- reading a zip

_SAFE_NAME = re.compile(r"[A-Za-z0-9._+\- ()]+")


def _safe_member(name):
    p = PurePosixPath(name)
    return (not p.is_absolute() and ".." not in p.parts and p.parts
            and p.parts[0] in ("themes", "backgrounds", "fonts", "current", "aether-blueprints")
            and all(_SAFE_NAME.fullmatch(part) and not part.startswith("..") for part in p.parts))


def read_zip(path):
    """The manifest of a share zip, after checking the whole zip: nothing outside its folders, sizes
    within limits. Raises BadZip with a plain reason. Nothing is unpacked."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise BadZip(f"it isn't an OmaSkins setup file ({e})")
    with z:
        infos = z.infolist()
        if len(infos) > MAX_MEMBERS:
            raise BadZip("too many files in it")
        total = 0
        for i in infos:
            if i.filename == MANIFEST or i.is_dir():
                continue
            if not _safe_member(i.filename):
                raise BadZip(f"it has a file in an unexpected place: {i.filename!r}")
            if i.file_size > MAX_MEMBER:
                raise BadZip(f"{i.filename} is too big")
            total += i.file_size
        if total > MAX_UNPACKED:
            raise BadZip("it unpacks to more than 4 GB")
        try:
            manifest = json.loads(z.read(MANIFEST))
        except (KeyError, ValueError):
            raise BadZip("it has no OmaSkins manifest (omaskins.json)")
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise BadZip("it wasn't made by this version of OmaSkins' Share")
    for key, kind in (("themes", list), ("backgrounds", list), ("fonts", list), ("rotation_sets", dict)):
        if not isinstance(manifest.get(key, kind()), kind):
            raise BadZip(f"its manifest is damaged ({key})")
    return manifest


# --------------------------------------------------------------------------- what can be imported

def name_key(name):
    """Names that are the same theme: case, spaces, hyphens and underscores don't count."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _ok_name(name):
    """A theme or file name from a zip that's safe to use as a folder or file name here."""
    return isinstance(name, str) and bool(_SAFE_NAME.fullmatch(name)) and not name.startswith(".")


def _themes(manifest):
    return {t["name"]: t for t in manifest.get("themes", []) if isinstance(t, dict) and _ok_name(t.get("name"))}


def _backgrounds(manifest):
    return [b for b in manifest.get("backgrounds", []) if isinstance(b, dict)
            and _ok_name(b.get("theme")) and _ok_name(b.get("file"))]


def _setup_themes(setup):
    """Theme names a Rotation setup relies on (its lists and the themes of its background choices)."""
    names = set()
    for lst in (setup.get("checked") or {}).values():
        names.update(n for n in lst if isinstance(n, str))
    names.update((setup.get("theme_picks") or {}).keys())
    for r in setup.get("solo_picks") or []:
        if isinstance(r, dict) and r.get("theme"):
            names.add(r["theme"])
    return names


def _installed():
    """{name_key: LocalTheme} for every theme here (the Aether working copy is part of its twin)."""
    out = {}
    for t in data.local_themes():
        out.setdefault(name_key(t.name), t)
    return out


def match(name, installed=None):
    """The theme here that `name` from a zip is (exactly, or nearly), or None."""
    installed = _installed() if installed is None else installed
    exact = next((t for t in installed.values() if t.name == name), None)
    return exact or installed.get(name_key(name))


def items(manifest):
    """The import window's rows: [{"id", "group", "label", "detail", "state", "needs"}], where state is
    "have" (already here: nothing to do), "new", "merge" (one of yours has that name: whatever it
    doesn't have yet is added to it) or "password" (a font package), and needs = ids that must come
    too (ticked and locked)."""
    installed = _installed()
    themes = _themes(manifest)
    out = []
    for name, t in sorted(themes.items()):
        kind = t.get("kind")
        mine = match(name, installed)
        detail = {"builtin": "built into Omarchy", "link": "from omarchy.org's list (downloaded)",
                  "aether": "made with Aether · in the file", "folder": "in the file",
                  "edited": "edited · in the file"}.get(kind, "")
        if kind == "builtin" or (kind == "link" and mine):
            state = "have"
        elif mine:
            state = "merge"
            detail = f"merges into your {data.display_name(mine.name)}: adds pictures you don't have"
        else:
            state = "new"
        out.append({"id": f"theme:{name}", "group": "Themes", "label": data.display_name(name),
                    "detail": detail, "state": state, "needs": []})
    for b in _backgrounds(manifest):
        out.append({"id": f"bg:{b.get('theme')}/{b.get('file')}", "group": "Your backgrounds",
                    "label": f"{data.display_name(str(b.get('theme')))} › {b.get('file')}",
                    "detail": f"{int(b.get('bytes', 0)) // 1024} KB", "state": "new", "needs": []})
    installed_fonts = {f.family for f in data.installed_fonts()}
    for f in manifest.get("fonts", []):
        fam = str(f.get("family"))
        if fam in installed_fonts:
            state = "have"
        elif f.get("source") == "repo":
            state = "password" if data.FONT_PKG_OK.fullmatch(str(f.get("package"))) else "have"
        else:
            state = "new" if f.get("files") else "have"
        out.append({"id": f"font:{fam}", "group": "Fonts", "label": fam,
                    "detail": (f"will be installed (Arch package {f.get('package')})" if state == "password"
                               else "will be added from the file" if state == "new"
                               else f"Arch package {f.get('package')}" if f.get("source") == "repo"
                               else "font files"), "state": state, "needs": []})
    out += _builtin_rows(manifest, themes)
    out += _font_drop_rows(manifest)
    existing_sets = data.rotation_sets()
    for name, setup in (manifest.get("rotation_sets") or {}).items():
        needs = [f"theme:{n}" for n in sorted(_setup_themes(setup)) if n in themes]
        out.append({"id": f"set:{name}", "group": "Rotation sets", "label": name,
                    "detail": (f"merges into your “{name}”" if name in existing_sets
                               else f"{len(_setup_themes(setup))} themes"),
                    "state": "merge" if name in existing_sets else "new", "needs": needs})
    rotation = manifest.get("rotation") or {}
    if "settings" in rotation:
        setup = rotation["settings"]
        needs = [f"theme:{n}" for n in sorted(_setup_themes(setup)) if n in themes]
        out.append({"id": "rotation", "group": "Rotation sets", "label": "The rotation in use (not a saved set)",
                    "detail": "comes in as a set named “Imported …”", "state": "new", "needs": needs})
    elif rotation.get("set") in (manifest.get("rotation_sets") or {}):
        out.append({"id": "rotation", "group": "Rotation sets", "label": f"In use there: “{rotation['set']}”",
                    "detail": "the set that was in use", "state": "new", "needs": [f"set:{rotation['set']}"]})
    corners = manifest.get("corners") or {}
    settings = [
        ("corners", "Rounded Corners", bool(corners), f"{corners.get('px')} px" if corners.get("on") else "off"),
        ("transparency", "Transparency", isinstance(manifest.get("transparency"), int),
         f"step {int(manifest.get('transparency') or 0) + 1} of {len(data.TRANSPARENCY_STEPS)}"),
        ("qt_apps", "Qt apps", isinstance(manifest.get("qt_apps"), bool), "on" if manifest.get("qt_apps") else "off"),
        ("text_size", "Text size", bool(manifest.get("text_size")), str(manifest.get("text_size") or "")),
        ("blueprints", "Aether blueprints", bool(manifest.get("aether_blueprints")),
         f"{len(manifest.get('aether_blueprints', []))}"),
    ]
    for key, label, present, detail in settings:
        if present:
            out.append({"id": key, "group": "Settings", "label": label, "detail": detail, "state": "new", "needs": []})
    return out


def _file_builtins(manifest, themes):
    """(installed there, hidden from OmaSkins there, removed entirely there). Files made before the two
    were kept apart list both as hidden: a hidden one there is still installed (it's among its themes)."""
    there = {n for n, t in themes.items() if t.get("kind") == "builtin"}
    hidden = set(manifest.get("hidden_builtins") or [])
    if "removed_builtins" in manifest:
        removed = set(manifest.get("removed_builtins") or [])
    else:
        removed, hidden = hidden - there, hidden & there
    return there, hidden, removed


def _builtin_rows(manifest, themes):
    """Built-in themes, to match the file: restore one it has (password), hide one it hid (no password),
    and for one it removed, the same question the Hide button asks: hide from OmaSkins, or remove entirely
    (password). The rows are always offered; ticking decides."""
    package = data.builtin_package()
    shipped = set(data.shipped_builtins(package))
    if not shipped:
        return []
    here = {n for n in shipped if (data.BUILTIN_THEMES / n).is_dir()}
    hidden_here = data.hidden_in_omaskins()
    there, hidden, removed = _file_builtins(manifest, themes)
    rows = []
    for n in sorted((there & shipped) - here):
        rows.append({"id": f"restore:{n}", "group": "Built-in themes", "label": f"Restore {data.display_name(n)}",
                     "detail": "will be reinstalled", "state": "password", "needs": []})
    for n in sorted(hidden & here - hidden_here - removed):
        rows.append({"id": f"hide:{n}", "group": "Built-in themes", "label": f"Hide {data.display_name(n)}",
                     "detail": "will be hidden from OmaSkins", "state": "new", "needs": []})
    for n in sorted(removed & here):
        rows.append({"id": f"builtin:{n}", "group": "Built-in themes", "label": data.display_name(n),
                     "detail": "will be hidden from OmaSkins", "state": "new", "needs": [],
                     # what the row says follows the answer picked under it
                     "detail_for": {f"hide:{n}": "will be hidden from OmaSkins", f"remove:{n}": "will be uninstalled"},
                     # (answer, its label, what will happen, password?, the longer explanation for its tooltip)
                     "choice": [(f"hide:{n}", "Hide from OmaSkins", "- will be hidden from OmaSkins", "",
                                 "No password. Stays installed, keeps getting Omarchy's updates, and still shows "
                                 "in Omarchy's own theme menu."),
                                (f"remove:{n}", "Remove entirely", "- will be uninstalled", "password",
                                 "It's part of Omarchy's system package: gone from Omarchy's menu too, and updates "
                                 "won't bring it back. Restore brings it back.")]})
    return rows


def _font_drop_rows(manifest):
    """Font packages installed here but not in the file: removing them makes it match the file exactly.
    Never ticked to begin with (a merge only adds), never Omarchy's own default font, never the font in
    use here or the one the file uses. Password (system packages)."""
    in_file = {str(f.get("package")) for f in manifest.get("fonts", [])}
    fonts = data.installed_fonts()
    in_use = data.fonts_in_use() | {(manifest.get("current") or {}).get("font")}
    keep = {data.OMARCHY_DEFAULT_FONT_PKG} | {f.package for f in fonts if f.current or f.family in in_use}
    pkgs = sorted({f.package for f in fonts if f.package and re.search(r"-nerd(-|$)", f.package)
                   and data.FONT_PKG_OK.fullmatch(f.package)} - in_file - keep)
    return [{"id": f"dropfont:{p}", "group": "Fonts", "label": f"Remove {p}",
             "detail": "will be uninstalled", "state": "password", "needs": [], "opt_in": True}
            for p in pkgs]


def with_needs(chosen, rows):
    """`chosen` plus everything the chosen rows need (and what those need)."""
    needs = {r["id"]: r["needs"] for r in rows}
    out, todo = set(), list(chosen)
    while todo:
        i = todo.pop()
        if i in out:
            continue
        out.add(i)
        todo.extend(needs.get(i, []))
    return out


# --------------------------------------------------------------------------- importing

def _free_name(folder, name):
    """`name` in `folder`, or name-2, name-3 … when that's taken (a different picture, same name)."""
    p, n = folder / name, 2
    stem, suffix = Path(name).stem, Path(name).suffix
    while p.exists():
        p = folder / f"{stem}-{n}{suffix}"
        n += 1
    return p


class _Pictures:
    """The pictures a theme here has, by content, so nothing comes in twice."""

    def __init__(self):
        self._by_theme = {}

    def of(self, theme):
        if theme.name not in self._by_theme:
            self._by_theme[theme.name] = {_sha1(b.path): b.path for b in data.backgrounds_for(theme)}
        return self._by_theme[theme.name]

    def add(self, theme, name, payload):
        """The picture's path here: the one already there, or a new file in your backgrounds for it."""
        have = self.of(theme)
        digest = hashlib.sha1(payload).hexdigest()
        if digest in have:
            return have[digest], False
        folder = data.USER_BACKGROUNDS / theme.name
        folder.mkdir(parents=True, exist_ok=True)
        dest = _free_name(folder, PurePosixPath(name).name)
        _write_new(dest, payload)
        have[digest] = dest
        return dest, True


def _unpack_theme(z, name, note):
    """A theme not here yet: themes/<name>/ into ~/.config/omarchy/themes, without the files that run code."""
    prefix = f"themes/{name}/"
    members = [i for i in z.infolist() if i.filename.startswith(prefix) and not i.is_dir()]
    dropped = sorted({PurePosixPath(i.filename).name for i in members if denied(PurePosixPath(i.filename).name)})
    stage = data.PARTIAL_THEMES / name
    shutil.rmtree(stage, ignore_errors=True)
    try:
        with _unfinished(stage):
            for i in members:
                if denied(PurePosixPath(i.filename).name):
                    continue
                out = stage / i.filename[len(prefix):]
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(i) as src, open(out, "wb") as f:
                    shutil.copyfileobj(src, f)
            data.USER_THEMES.mkdir(parents=True, exist_ok=True)
            stage.rename(data.USER_THEMES / name)
    finally:
        try:
            data.PARTIAL_THEMES.rmdir()   # only when empty: a theme download may be using it
        except OSError:
            pass
    if dropped:
        note.append(f"{data.display_name(name)}: left out {', '.join(dropped)} (they run code; Omarchy "
                    "does the same for downloaded themes)")


def _merge_setups(mine, theirs):
    """Two Rotation sets with one name, as one: every theme either list has; for each theme, the
    backgrounds either picked (a theme with no picks means all of its backgrounds, so it stays all);
    your interval and switches."""
    out = dict(mine)
    checked = {}
    for period in set(mine.get("checked") or {}) | set(theirs.get("checked") or {}):
        a = list((mine.get("checked") or {}).get(period, []))
        checked[period] = a + [n for n in (theirs.get("checked") or {}).get(period, []) if n not in a]
    out["checked"] = checked
    def listed(setup):
        return {n for lst in (setup.get("checked") or {}).values() for n in lst}
    a, b = mine.get("theme_picks") or {}, theirs.get("theme_picks") or {}
    picks = {}
    for theme in set(a) | set(b):
        if theme in a and theme in b:
            picks[theme] = sorted(set(a[theme]) | set(b[theme]))
        elif theme in a and theme not in listed(theirs):
            picks[theme] = a[theme]
        elif theme in b and theme not in listed(mine):
            picks[theme] = b[theme]
        # else one side shows all of that theme's backgrounds, so the combined set does too
    out["theme_picks"] = picks
    if mine.get("solo_picks") is None or theirs.get("solo_picks") is None:
        out["solo_picks"] = mine.get("solo_picks") if theirs.get("solo_picks") is None else theirs.get("solo_picks")
    else:
        out["solo_picks"] = sorted(set(mine["solo_picks"]) | set(theirs["solo_picks"]))
    return out


def run_import(zip_path, manifest, chosen, apply, progress=None):
    """Logged start and end (~/.local/state/omaskins/import.log), whatever happens in between."""
    _log(f"import {Path(zip_path).name} ({'also apply' if apply else 'just import'}): ticked "
         + ", ".join(sorted(chosen)))
    try:
        done, note, failed = _run_import(zip_path, manifest, chosen, apply, progress)
    except Exception as e:
        import traceback
        _log("import STOPPED: " + "".join(traceback.format_exception(e)).strip().replace("\n", "\n    "))
        raise
    _log(f"import finished: {len(done)} done ({'; '.join(done) or '-'}); not done: {'; '.join(note) or '-'}")
    return done, note, failed


def _run_import(zip_path, manifest, chosen, apply, progress=None):
    """Bring in what's chosen (ids from items()); apply = also mirror the look and the rotation.
    Runs on a worker thread. progress({"step", "total", "text", "download"}) hears every step: the
    whole import is counted up front (each theme, background, font, set, setting and the apply at
    the end, one step each), so step/total is how far along it really is; "download" is None, or
    {"pct", "got" (bytes), "rate" (bytes/s or None)} while a theme is being downloaded.
    Returns (lines of what was done, notes, names of themes that couldn't be downloaded)."""
    from . import run
    rows = items(manifest)
    chosen = with_needs(chosen, rows)
    state = {r["id"]: r["state"] for r in rows}
    note, done = [], []
    themes = _themes(manifest)
    installed = _installed()
    as_here = {name: mine.name for name in themes if (mine := match(name, installed))}   # zip name -> here
    pics = {}         # (theme in the zip, file, own) -> the picture's path here
    pictures = _Pictures()
    failed = []
    total, n = 1, 0

    def report(text, download=None, cancel=False):
        if progress:
            progress({"step": max(1, min(n, total)), "total": total, "text": text, "download": download,
                      "cancel": cancel})

    def step(text):
        """The next step begins."""
        nonlocal n
        n += 1
        report(text)

    def download_progress(text):
        """Callbacks for one theme download: git's percent, and what has arrived and how fast."""
        dl = {"pct": 0, "got": 0, "rate": None}

        def pct(value):
            dl["pct"] = value
            report(text, dict(dl))

        def transfer(got, rate):
            dl["got"], dl["rate"] = got, rate
            report(text, dict(dl))
        return pct, transfer

    downloads = [name for name, t in sorted(themes.items()) if f"theme:{name}" in chosen
                 and t.get("kind") == "link" and not match(name, installed)]

    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        # Every step this import will take, counted before the first one.
        bgs = [b for b in _backgrounds(manifest)
               if f"bg:{b.get('theme')}/{b.get('file')}" in chosen and b.get("zip") in names]
        fonts = [f for f in manifest.get("fonts", []) if f"font:{f.get('family')}" in chosen]
        add_pkgs = sorted({str(f["package"]) for f in fonts if state.get(f"font:{f.get('family')}") == "password"})
        drop_pkgs = sorted(i.split(":", 1)[1] for i in chosen if i.startswith("dropfont:"))
        remove_themes = sorted(i.split(":", 1)[1] for i in chosen if i.startswith("remove:"))
        restore_themes = sorted(i.split(":", 1)[1] for i in chosen if i.startswith("restore:"))
        hide_themes = sorted(i.split(":", 1)[1] for i in chosen if i.startswith("hide:"))
        needs_password = bool(add_pkgs or drop_pkgs or remove_themes or restore_themes)
        total = max(1, sum(f"theme:{name}" in chosen for name in themes) + len(bgs) + len(fonts)
                    + ("blueprints" in chosen)
                    + sum(f"set:{name}" in chosen for name in (manifest.get("rotation_sets") or {}))
                    + ("rotation" in chosen) + bool(hide_themes) + needs_password + bool(apply))
        for name, t in sorted(themes.items()):
            if f"theme:{name}" not in chosen:
                continue
            kind, mine = t.get("kind"), match(name, installed)
            if kind == "link" and not mine:
                what = f"Downloading {data.display_name(name)} ({downloads.index(name) + 1} of {len(downloads)})"
            else:
                what = f"Adding {data.display_name(name)}"
            step(what)
            if kind == "builtin":
                continue
            if kind == "link":
                if mine:
                    continue
                c = data.CommunityTheme(name, str(t.get("repo", "")), "")
                try:
                    # With progress: read as it arrives, and only given up on when nothing arrives.
                    on_pct, on_transfer = download_progress(what)
                    report(what, {"pct": 0, "got": 0, "rate": None})
                    run.perform(data.add_theme_action(c).steps, on_pct, transfer=on_transfer)
                    done.append(f"Downloaded {data.display_name(name)}")
                except Exception as e:  # noqa: BLE001 (one theme failing doesn't stop the rest)
                    failed.append(name)
                    note.append(f"{data.display_name(name)}: couldn't download it ({e})")
                continue
            if not mine:
                _unpack_theme(z, name, note)
                as_here[name] = name
                done.append(f"Added {data.display_name(name)}")
                continue
            # One of yours has this name: add the pictures it doesn't have yet; your colours stay.
            prefix, added = f"themes/{name}/backgrounds/", 0
            for member in sorted(m for m in names if m.startswith(prefix) and "/" not in m[len(prefix):]):
                if PurePosixPath(member).suffix.lower() not in data.IMAGE_EXT:
                    continue
                path, new = pictures.add(mine, member, z.read(member))
                pics[(name, PurePosixPath(member).name, False)] = path
                added += new
            if added:
                done.append(f"{data.display_name(mine.name)}: added {added} new picture{'s' * (added != 1)}")
        installed = _installed()
        for b in bgs:
            step(f"Adding background {b['file']}")
            theme = match(as_here.get(b["theme"], b["theme"]), installed)
            if not theme:   # its theme isn't here: kept for when it is (Omarchy shows them by folder name)
                theme = data.LocalTheme(b["theme"], data.USER_THEMES / b["theme"], False)
            path, new = pictures.add(theme, b["file"], z.read(b["zip"]))
            pics[(b["theme"], b["file"], True)] = path
            if new:
                done.append(f"Added background {data.display_name(theme.name)} › {path.name}")
        # Fonts: files straight in (no password); packages later, with everything else that needs the
        # password, in one terminal.
        copied = 0
        for f in fonts:
            fid = f"font:{f.get('family')}"
            step(f"Fonts: {f.get('family')}")
            if state.get(fid) == "new":
                for name in f.get("files", []):
                    dest = IMPORTED_FONTS / PurePosixPath(name).name
                    if name in names and not dest.exists():
                        _write_new(dest, z.read(name))
                        copied += 1
        if copied:
            subprocess.run(["fc-cache", "-f", str(IMPORTED_FONTS)], capture_output=True, timeout=120,
                           env=data.child_env())
            done.append(f"Added {copied} font file{'s' * (copied != 1)}")
        if "blueprints" in chosen:
            step("Adding Aether blueprints")
            for name in manifest.get("aether_blueprints", []):
                dest = data.AETHER_BLUEPRINTS / PurePosixPath(name).name
                if name in names and not dest.exists():   # yours with that name stays as it is
                    _write_new(dest, z.read(name))
        current_bg = (manifest.get("current") or {}).get("background") or {}
        if apply and current_bg.get("zip") in names:
            theme = match(as_here.get((manifest.get("current") or {}).get("theme", ""), ""))
            if theme:
                pics["current"] = pictures.add(theme, current_bg["zip"], z.read(current_bg["zip"]))[0]

    def here(setup):
        """A set from the zip in this machine's names and paths."""
        def path(ref):
            if not isinstance(ref, dict):
                return None
            got = pics.get((ref.get("theme"), ref.get("file"), bool(ref.get("own"))))
            return str(got) if got else (str(p) if (p := path_of(dict(ref, theme=as_here.get(ref.get("theme"), ref.get("theme"))))) else None)
        s = dict(setup)
        s["checked"] = {p: list(dict.fromkeys(as_here.get(x, x) for x in lst))
                        for p, lst in (setup.get("checked") or {}).items()}
        s["theme_picks"] = {as_here.get(t, t): [x for x in map(path, refs) if x]
                            for t, refs in (setup.get("theme_picks") or {}).items() if isinstance(refs, list)}
        if isinstance(setup.get("solo_picks"), list):
            s["solo_picks"] = [x for x in map(path, setup["solo_picks"]) if x]
        return s

    sets = data.rotation_sets()
    before = json.dumps(sets, sort_keys=True)
    for name, setup in (manifest.get("rotation_sets") or {}).items():
        if f"set:{name}" not in chosen:
            continue
        step(f"Rotation set “{name}”")
        theirs = here(setup)
        if name in sets:
            merged = _merge_setups(sets[name], theirs)
            if json.dumps(merged, sort_keys=True) != json.dumps(sets[name], sort_keys=True):
                sets[name] = merged
                done.append(f"Rotation set “{name}”: combined with the imported one")
        else:
            sets[name] = theirs
            done.append(f"Added Rotation set “{name}”")
    rotation = manifest.get("rotation") or {}
    in_use = None
    if "rotation" in chosen:
        step("The rotation that was in use")
        if "settings" in rotation:
            theirs = here(rotation["settings"])
            same = next((k for k, v in sets.items() if json.dumps(v, sort_keys=True) == json.dumps(theirs, sort_keys=True)), None)
            in_use = same or f"Imported {time.strftime('%Y-%m-%d %H:%M')}"
            if not same:
                sets[in_use] = theirs
                done.append(f"Added the rotation that was in use as the set “{in_use}”")
        elif rotation.get("set"):
            in_use = rotation["set"]
    if json.dumps(sets, sort_keys=True) != before:
        data._write_sets(sets)

    if hide_themes:
        step("Hiding built-in themes from OmaSkins")
        current = data.current_theme_name()
        for name in hide_themes:
            if name != current and (data.BUILTIN_THEMES / name).is_dir():
                data.set_hidden_in_omaskins(name, True)
                done.append(f"Hid {data.display_name(name)} from OmaSkins")
    if needs_password:
        step("Waiting for your password: one terminal does all of it")
        _password_work(add_pkgs, drop_pkgs, remove_themes, restore_themes, done, note, report)

    if apply:
        step("Applying the look (theme, background, font, corners, rotation)")
        done += apply_look(manifest, chosen, as_here, pics, in_use, note)
    n = total + 1   # past the last step: the whole bar
    if progress:
        progress({"step": total, "total": total, "text": "Finished", "download": None, "finished": True})
    return done, note, failed


_PASSWORD = {"cmd": None, "cancelled": False}   # the password terminal now open, for Cancel
IMPORT_LOG = data.OMASKINS_STATE / "import.log"


def _log(line):
    """One line in ~/.local/state/omaskins/import.log: how each import's password step ended, and why."""
    try:
        IMPORT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with IMPORT_LOG.open("a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def cancel_password_step():
    """Cancel in the Importing window: close the password terminal (nothing in it gets done)."""
    from . import run
    cmd = _PASSWORD["cmd"]
    if cmd:
        _PASSWORD["cancelled"] = True
        run.close_terminal(cmd)


def _password_work(add_pkgs, drop_pkgs, remove_themes, restore_themes, done, note, report=None):
    """Everything that needs the password, in ONE terminal, so it's typed once (owner, 2026-10-02): font
    packages added and removed, built-ins removed and restored. Each item reported on its own."""
    from . import run
    package = data.builtin_package()
    unhide, reinstall = [], []
    for n in restore_themes:   # the same choice the Restore button makes
        current = data.package_version(package)
        if data.builtin_held_aside(n) and current and current == data.removed_at_version(n):
            unhide.append(n)
        else:
            reinstall.append(n)
    cmd = data.password_terminal_command(add_pkgs, drop_pkgs, remove_themes, unhide, reinstall, package)
    lists = data.parse_password_terminal_head(cmd)
    _PASSWORD.update(cmd=cmd, cancelled=False)
    run.clear_password_marks()
    try:
        run.perform((("terminal", cmd),))
        if report:
            report("Type your password in the terminal (Ctrl+C there, or Cancel here, stops it)", None, True)
        result, why = run.wait_import_password(lists)
    finally:
        _PASSWORD["cmd"] = None
        run.clear_password_marks()
    _log(f"password terminal: {why}; " + ", ".join(f"{k[0]} {k[1]}={'ok' if v else 'NOT DONE'}"
                                                  for k, v in sorted(result.items())))
    said = {"add-font": ("Installed {}", "{}: not installed"),
            "drop-font": ("Removed {}", "{}: not removed"),
            "remove-theme": ("Removed {} entirely", "{}: not removed"),
            "restore-theme": ("Restored {}", "{}: not restored")}
    for (kind, name), ok in sorted(result.items()):
        title = data.display_name(name) if kind.endswith("theme") else name
        if ok:
            done.append(said[kind][0].format(title))
            if kind == "remove-theme":
                data.note_removed_version(name, package)
            elif kind == "restore-theme":
                data.set_hidden_in_omaskins(name, False)
                data.forget_removed_version(name)
        elif _PASSWORD["cancelled"]:
            note.append(said[kind][1].format(title) + " (cancelled)")
        else:
            note.append(said[kind][1].format(title) + " (the terminal finished without it: a wrong or "
                        "cancelled password, or a download that failed)")


def apply_look(manifest, chosen, as_here, pics, in_use, note):
    """"Also apply": the rotation that was in use, corners, text size and font, then the theme and the
    very background it had."""
    from . import run
    done = []
    current = manifest.get("current") or {}
    if in_use and in_use in data.rotation_sets():
        data.use_rotation_set(in_use, data.current_theme_name())
        done.append(f"Rotation: “{in_use}”")
    if "corners" in chosen and manifest.get("corners"):
        c = manifest["corners"]
        run.perform(data.corners_action(bool(c.get("on")), int(c.get("px") or data.CORNERS_DEFAULT)).steps)
        done.append("Corners as they were there")
    step = manifest.get("transparency")
    if "transparency" in chosen and isinstance(step, int) and 0 <= step < len(data.TRANSPARENCY_STEPS):
        run.perform(data.transparency_action(step).steps)
        done.append(f"Transparency: step {step + 1} of {len(data.TRANSPARENCY_STEPS)}")
    if "qt_apps" in chosen and isinstance(manifest.get("qt_apps"), bool):
        run.perform(data.qt_apps_action(manifest["qt_apps"]).steps)
        done.append(f"Qt apps: {'on' if manifest['qt_apps'] else 'off'}")
    font = current.get("font")
    if font and font in {f.family for f in data.installed_fonts()}:
        if "text_size" in chosen and manifest.get("text_size"):
            data.TEXT_SIZE_STATE.parent.mkdir(parents=True, exist_ok=True)
            data.TEXT_SIZE_STATE.write_text(json.dumps({"base": int(manifest["text_size"]), "wrote": None}))
        run.perform((("run", ["omarchy-font-set", font]), ("font_size", font)))
        done.append(f"Font: {font}")
    theme = match(as_here.get(current.get("theme", ""), current.get("theme", ""))) if current.get("theme") else None
    if theme:
        bg = current.get("background") or {}
        path = pics.get("current") if bg.get("zip") else (
            pics.get((bg.get("theme"), bg.get("file"), bool(bg.get("own"))))
            or path_of(dict(bg, theme=as_here.get(bg.get("theme"), bg.get("theme")))) if bg else None)
        run.set_theme(data.theme_folder_for(theme.name), path)
        done.append(f"Theme: {data.display_name(theme.name)}" + (f", background {Path(path).name}" if path else ""))
    elif current.get("theme"):
        note.append(f"The theme in use there ({data.display_name(current['theme'])}) isn't here, so it wasn't applied")
    if manifest.get("sort") in ("top", "az"):
        data.save_sort_mode(manifest["sort"])
    return done
