"""OmaSkins in Omarchy's own menu: Style › OmaSkins (added while the plugin is on).

Omarchy reads extra rows from ~/.config/omarchy/extensions/omarchy-menu.jsonc (yours; no password) and
reloads it live. Rows from there always come after Omarchy's own in their menu (its merge keeps its own
order and appends new ids), so the last row of Style is as high as a row can go without replacing one of
Omarchy's. Our row sits between marker comments (full-line comments, which Omarchy's reader skips);
anything else in the file is left alone. Service.qml removes the block when the plugin is turned off or
removed (inline, since `omarchy plugin remove` deletes this folder right after).
"""

import json
import os
from pathlib import Path

from . import data

MENU_FILE = Path(os.environ.get("XDG_CONFIG_HOME", data.HOME / ".config")) / "omarchy/extensions/omarchy-menu.jsonc"
START = "  // >>> OmaSkins (added by the OmaSkins plugin while it's on)"
END = "  // <<< OmaSkins"
ROW_ID = "style.omaskins"
PALETTE = "\U000F03D8"


def row(launcher):
    entry = {"icon": PALETTE, "label": "OmaSkins",
             "description": "Themes, backgrounds, fonts, rotation and transparency",
             "action": f"uwsm-app -- {json.dumps(str(launcher))[1:-1]}"}
    return f'  "{ROW_ID}": {json.dumps(entry, ensure_ascii=False)},'


def _without_block(lines):
    out, inside = [], False
    for line in lines:
        if line.rstrip() == START:
            inside = True
            continue
        if inside:
            if line.rstrip() == END:
                inside = False
            continue
        out.append(line)
    return out


def add_row(launcher):
    """Our block right after the file's opening brace. True if the file changed."""
    try:
        text = MENU_FILE.read_text()
    except OSError:
        text = "{\n}\n"
    lines = _without_block(text.splitlines())
    at = next((i for i, l in enumerate(lines) if l.strip().startswith("{")), None)
    if at is None:
        return False   # not a file we understand: leave it alone
    new = lines[:at + 1] + [START, row(launcher), END] + lines[at + 1:]
    new_text = "\n".join(new) + "\n"
    if new_text == text:
        return False
    MENU_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MENU_FILE.with_name(".omarchy-menu.jsonc.omaskins-tmp")
    tmp.write_text(new_text)
    tmp.replace(MENU_FILE)
    return True


def remove_row():
    try:
        text = MENU_FILE.read_text()
    except OSError:
        return False
    new_text = "\n".join(_without_block(text.splitlines())) + "\n"
    if new_text == text:
        return False
    tmp = MENU_FILE.with_name(".omarchy-menu.jsonc.omaskins-tmp")
    tmp.write_text(new_text)
    tmp.replace(MENU_FILE)
    return True
