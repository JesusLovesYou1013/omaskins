"""Omarchy theme -> GTK CSS.

The app takes every colour, border and font size from the *current* Omarchy
theme, the same tokens the Omarchy shell menu uses:

    ~/.local/state/omarchy/current/theme/colors.toml   palette (accent, red, ...)
    ~/.local/state/omarchy/current/theme/shell.toml    [menu] [controls] [popups]
    ~/.config/omarchy/shell.toml                       user overrides (font size)

`load_theme()` is cheap, so the window simply calls it again whenever those
files change and reloads the CSS.
"""

import os
import re
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

HOME = Path.home()
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "omarchy" / "current"
THEME_DIR = STATE_DIR / "theme"
USER_CONFIG_DIR = HOME / ".config/omarchy"
USER_SHELL_TOML = USER_CONFIG_DIR / "shell.toml"

# Only used when Omarchy's theme files can't be read at all.
FALLBACK_COLORS = {
    "mode": "dark",
    "accent": "#7daea3",
    "background": "#282828",
    "foreground": "#d4be98",
    "muted": "#665c54",
    "red": "#ea6962",
    "green": "#a9b665",
    "yellow": "#d8a657",
}


def _read_toml(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _merge(base, over):
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def resolve_color(value, hypr, fallback):
    """Turn a shell.toml colour value into '#rrggbb'.

    Handles '#rrggbb[aa]', references like 'hyprland.active-border', and
    Hyprland gradients ('rgba(33ccffee) rgba(00ff99ee) 45deg') by taking the
    first stop, since GTK can't paint a gradient border.
    """
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    if value.startswith("hyprland."):
        return resolve_color((hypr or {}).get(value.split(".", 1)[1]), {}, fallback)
    m = re.match(r"^#([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?$", value)
    if m:
        return "#" + m.group(1).lower()
    m = re.search(r"rgba?\(([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?\)", value)
    if m:
        return "#" + m.group(1).lower()
    m = re.search(r"0x[0-9a-fA-F]{2}([0-9a-fA-F]{6})", value)
    if m:
        return "#" + m.group(1).lower()
    return fallback


def rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {max(0.0, min(1.0, float(alpha))):.3f})"


def _luminance(hex_color):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _num(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def current_font():
    try:
        out = subprocess.run(["omarchy-font-current"], capture_output=True, text=True, timeout=3).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    return "monospace"


def load_theme():
    colors = {**FALLBACK_COLORS, **_read_toml(THEME_DIR / "colors.toml")}
    shell = _merge(_read_toml(THEME_DIR / "shell.toml"), _read_toml(USER_SHELL_TOML))
    hypr = shell.get("hyprland", {})
    menu = shell.get("menu", {})
    ctrl = shell.get("controls", {})
    popups = shell.get("popups", {})

    bg = resolve_color(menu.get("background"), hypr, resolve_color(colors.get("background"), {}, "#282828"))
    fg = resolve_color(menu.get("text"), hypr, resolve_color(colors.get("foreground"), {}, "#d4be98"))
    accent = resolve_color(colors.get("accent"), {}, fg)
    mode = colors.get("mode") or ("light" if _luminance(bg) > 0.5 else "dark")

    def c(name, fallback):
        return resolve_color(colors.get(name), {}, fallback)

    def ctl(state, key, default):
        return _num(ctrl.get(f"{state}-{key}"), default)

    return SimpleNamespace(
        name=(STATE_DIR / "theme.name").read_text().strip() if (STATE_DIR / "theme.name").exists() else "",
        mode=mode,
        bg=bg,
        fg=fg,
        accent=accent,
        border=resolve_color(menu.get("border"), hypr, fg),
        border_alpha=_num(menu.get("border-alpha"), 1.0),
        border_width=int(_num(menu.get("border-width", popups.get("border-width")), 2)),
        selected_bg=resolve_color(menu.get("selected-background"), hypr, fg),
        selected_bg_alpha=_num(menu.get("selected-background-alpha"), 0.08),
        selected_text=resolve_color(menu.get("selected-text"), hypr, accent),
        selected_border=resolve_color(menu.get("selected-border"), hypr, fg),
        selected_border_alpha=_num(menu.get("selected-border-alpha"), 0.25),
        red=c("red", "#ea6962"),
        green=c("green", "#a9b665"),
        yellow=c("yellow", "#d8a657"),
        ctl_fill=ctl("normal", "fill-alpha", 0.04),
        ctl_border=resolve_color(ctrl.get("normal-border"), hypr, fg),
        ctl_border_alpha=ctl("normal", "border-alpha", 0.4),
        ctl_border_width=int(ctl("normal", "border-width", 1)),
        hover_fill=ctl("hover-cursor", "fill-alpha", 0.08),
        hover_border_alpha=ctl("hover-cursor", "border-alpha", 0.25),
        pressed_fill=_num(ctrl.get("pressed-fill-alpha"), 0.22),
        font_px=int(_num(shell.get("font", {}).get("base-size"), 12)),
        font_family=current_font(),
    )


def build_css(t):
    """GTK4 / libadwaita CSS for the theme `t` (from load_theme)."""
    fg, bg, accent = t.fg, t.bg, t.accent
    dim = rgba(fg, 0.62)
    line = rgba(fg, 0.14)
    frame = rgba(t.border, t.border_alpha)
    ctl_border = rgba(t.ctl_border, t.ctl_border_alpha)
    hover_border = rgba(t.ctl_border, max(t.hover_border_alpha, 0.5))
    sel_fill = rgba(t.selected_bg, t.selected_bg_alpha)
    sel_border = rgba(t.selected_border, t.selected_border_alpha)
    bw = t.ctl_border_width
    family = t.font_family.replace('"', "")

    return f"""
/* Adwaita named colours -> Omarchy palette, so every stock widget follows the theme */
@define-color window_bg_color {bg};
@define-color window_fg_color {fg};
@define-color view_bg_color {bg};
@define-color view_fg_color {fg};
@define-color headerbar_bg_color {bg};
@define-color headerbar_fg_color {fg};
@define-color popover_bg_color {bg};
@define-color popover_fg_color {fg};
@define-color dialog_bg_color {bg};
@define-color dialog_fg_color {fg};
@define-color card_bg_color {rgba(fg, 0.05)};
@define-color card_fg_color {fg};
@define-color accent_color {accent};
@define-color accent_bg_color {accent};
@define-color accent_fg_color {bg};
@define-color destructive_color {t.red};
@define-color destructive_bg_color {t.red};
@define-color destructive_fg_color {bg};
@define-color success_color {t.green};
@define-color warning_color {t.yellow};
@define-color error_color {t.red};

* {{
  font-family: "{family}", monospace;
  font-size: {t.font_px}px;
}}

window.omaskins, window.omaskins.csd {{
  background: {bg};
  color: {fg};
  border-radius: 0;
  box-shadow: none;
}}

.frame-root {{
  background: {bg};
  border: {t.border_width}px solid {frame};
  border-radius: 0;
}}

.window-title {{ color: {dim}; padding: 14px 18px 4px 18px; }}

/* tabs */
.tabbar {{ padding: 2px 18px 0 18px; border-bottom: 1px solid {line}; }}
button.tab {{
  background: none; background-image: none; box-shadow: none; outline: none;
  border: none; border-bottom: 2px solid transparent; border-radius: 0;
  color: {dim}; padding: 8px 14px; margin: 0 4px -1px 0; min-height: 0;
}}
button.tab:hover {{ color: {fg}; background: {rgba(fg, t.hover_fill)}; }}
button.tab:checked {{ color: {t.selected_text}; border-bottom-color: {accent}; background: {sel_fill}; }}
button.tab .count {{ color: {dim}; margin-left: 6px; }}

/* search + generic controls */
entry, searchentry, searchentry > text {{
  background: {rgba(fg, t.ctl_fill)}; color: {fg};
  border: {bw}px solid {ctl_border}; border-radius: 0; box-shadow: none; outline: none;
  caret-color: {accent};
}}
searchentry {{ min-width: 260px; padding: 2px 6px; }}
searchentry:focus-within {{ border-color: {accent}; }}
searchentry > text {{ border: none; background: none; padding: 2px; }}
searchentry image {{ color: {dim}; }}
selection {{ background: {rgba(accent, 0.35)}; color: {fg}; }}

button.omarchy-btn, dropdown.version-drop > button {{
  background: {rgba(fg, t.ctl_fill)}; background-image: none; color: {fg};
  border: {bw}px solid {ctl_border}; border-radius: 0; box-shadow: none; outline: none;
  padding: 4px 12px; min-height: 0; min-width: 0;
}}
button.omarchy-btn:hover, dropdown.version-drop > button:hover {{
  background: {rgba(fg, t.hover_fill)}; border-color: {hover_border}; color: {t.selected_text};
}}
button.omarchy-btn:active, dropdown.version-drop > button:active {{ background: {rgba(fg, t.pressed_fill)}; }}
button.omarchy-btn:focus-visible, dropdown.version-drop > button:focus-visible, button.tab:focus-visible {{
  outline: 1px solid {accent}; outline-offset: -3px;
}}
button.omarchy-btn:disabled, dropdown.version-drop > button:disabled {{ opacity: 0.38; }}
button.omarchy-btn.primary {{ border-color: {accent}; color: {accent}; }}
button.omarchy-btn.primary:hover {{ background: {rgba(accent, 0.16)}; color: {accent}; border-color: {accent}; }}
button.omarchy-btn.danger:hover {{ background: {rgba(t.red, 0.14)}; color: {t.red}; border-color: {t.red}; }}
button.omarchy-btn.flat {{ border-color: transparent; background: none; }}
button.icon-btn {{
  background: none; background-image: none; box-shadow: none; border: {bw}px solid transparent;
  border-radius: 0; color: {dim}; min-height: 0; min-width: 0; padding: 4px 8px;
}}
button.icon-btn:hover {{ color: {fg}; background: {rgba(fg, t.hover_fill)}; border-color: {ctl_border}; }}

popover.background > contents, popover > contents {{
  background: {bg}; color: {fg}; border: 1px solid {frame}; border-radius: 0; box-shadow: none; padding: 2px;
}}
popover modelbutton, popover listview > row, popover row {{ border-radius: 0; padding: 4px 8px; }}
popover row:hover, popover listview > row:hover {{ background: {rgba(fg, t.hover_fill)}; }}
popover row:selected, popover listview > row:selected {{ background: {sel_fill}; color: {t.selected_text}; }}
tooltip, tooltip.background {{ background: {bg}; color: {fg}; border: 1px solid {frame}; border-radius: 0; box-shadow: none; }}
tooltip label {{ color: {fg}; }}

/* list */
scrolledwindow, listview, list {{ background: none; }}
listview > row {{
  padding: 0; margin: 0; border-radius: 0; background: none; box-shadow: none; outline: none;
  border-bottom: 1px solid {line};
}}
listview > row:hover {{ background: {rgba(fg, t.hover_fill / 2)}; }}
listview > row:focus-visible {{ outline: 1px solid {sel_border}; outline-offset: -2px; background: {sel_fill}; }}
.plugin-row {{ padding: 12px 18px; }}
.plugin-name {{ font-weight: bold; font-size: {t.font_px * 1.15:.1f}px; color: {fg}; }}
.dim {{ color: {dim}; }}
.small {{ font-size: {t.font_px * 0.9:.1f}px; }}
.section-title {{ color: {accent}; font-weight: bold; padding-top: 6px; }}
.kv-key {{ color: {dim}; }}
.mono {{ font-family: "{family}", monospace; }}

/* installed mark: a plain accent check, deliberately NOT a checkbox */
.installed-mark {{ color: {accent}; font-size: {t.font_px * 1.5:.1f}px; min-width: 24px; }}

.badge {{ padding: 0 6px; border: 1px solid {rgba(fg, 0.3)}; color: {dim}; font-size: {t.font_px * 0.85:.1f}px; }}
.badge-enabled {{ color: {t.green}; border-color: {rgba(t.green, 0.6)}; }}
.badge-disabled {{ color: {dim}; }}
.badge-verified {{ color: {accent}; border-color: {rgba(accent, 0.6)}; }}
.badge-warn {{ color: {t.yellow}; border-color: {rgba(t.yellow, 0.6)}; }}
.badge-builtin {{ color: {t.selected_text}; border-color: {rgba(t.selected_text, 0.5)}; }}

.statusbar {{ padding: 6px 18px; border-top: 1px solid {line}; color: {dim}; }}
.banner {{ background: {rgba(t.yellow, 0.12)}; color: {t.yellow}; padding: 6px 18px; }}
.empty {{ color: {dim}; padding: 40px; }}

.carousel-frame {{ background: {rgba(fg, 0.05)}; border: 1px solid {frame}; }}
.carousel-arrow {{
  background: {rgba(bg, 0.4)}; color: {fg}; border: none; border-radius: 0; box-shadow: none;
  min-width: 34px; min-height: 34px; margin: 10px; padding: 0;
  font-size: {t.font_px * 1.4:.1f}px; opacity: 0.5;
}}
.carousel-arrow:hover {{ opacity: 1; background: {rgba(bg, 0.7)}; color: {accent}; }}
.carousel-arrow:disabled {{ opacity: 0; }}
.carousel-dots {{ margin-bottom: 10px; }}
.carousel-dot {{ color: {rgba(fg, 0.4)}; font-size: 10px; }}
.carousel-dot.active {{ color: {accent}; }}
.notice {{ border: 1px solid {rgba(t.yellow, 0.5)}; background: {rgba(t.yellow, 0.07)}; padding: 10px 12px; }}

link, label link, a {{ color: {accent}; text-decoration: none; }}
label link:hover {{ text-decoration: underline; }}

/* slim overlay scrollbars: a 3px line that fades out when idle, 6px while hovered or dragged.
   Both states are spelled out because this sheet outranks Adwaita's, whose thin state it replaced. */
scrollbar, scrollbar trough {{ background: none; border: none; box-shadow: none; }}
scrollbar slider {{
  background: {rgba(fg, 0.3)}; border: none; border-radius: 0; margin: 2px;
  min-width: 3px; min-height: 3px;
}}
scrollbar.vertical slider {{ min-height: 30px; }}
scrollbar.horizontal slider {{ min-width: 30px; }}
scrollbar.vertical.hovering slider, scrollbar.vertical.dragging slider {{ min-width: 6px; }}
scrollbar.horizontal.hovering slider, scrollbar.horizontal.dragging slider {{ min-height: 6px; }}
scrollbar.hovering slider, scrollbar.dragging slider {{ background: {rgba(fg, 0.5)}; }}
scrollbar button {{ min-width: 0; min-height: 0; padding: 0; border: none; background: none; }}

dialog, dialog.background, window.dialog {{ border-radius: 0; }}
dialog .dialog-host, floating-sheet, dialog sheet {{ border-radius: 0; }}
toast {{ background: {bg}; color: {fg}; border: 1px solid {frame}; border-radius: 0; }}

/* ---- OmaSkins additions ---- */
.prototype-banner {{ background: {rgba(accent, 0.12)}; color: {accent}; padding: 6px 18px; }}

/* sub-tabs (Browse / Installed) sit under the main tabs, smaller and quieter */
.subtabbar {{ padding: 6px 18px 6px 18px; }}
button.subtab {{
  background: none; background-image: none; box-shadow: none; outline: none;
  border: 1px solid transparent; border-radius: 0; color: {dim}; padding: 3px 12px; margin-right: 6px; min-height: 0;
}}
button.subtab:hover {{ color: {fg}; border-color: {ctl_border}; }}
button.subtab:checked {{ color: {t.selected_text}; border-color: {accent}; background: {sel_fill}; }}
button.subtab .count {{ color: {dim}; margin-left: 6px; }}

/* cards (theme + background grids) */
gridview, flowbox {{ background: none; }}
flowboxchild {{ padding: 0; border-radius: 0; outline: none; }}
flowboxchild:focus-visible {{ outline: 1px solid {sel_border}; outline-offset: -2px; }}
gridview > child {{ padding: 0; margin: 0; border-radius: 0; background: none; outline: none; }}
gridview > child:focus-visible {{ outline: 1px solid {sel_border}; outline-offset: -2px; }}
.tile {{ padding: 10px; border: 1px solid transparent; border-radius: 0; box-shadow: none; background: none; }}
.tile:hover {{ background: {rgba(fg, t.hover_fill)}; border-color: {line}; }}
.tile.current {{ border-color: {rgba(accent, 0.7)}; background: {sel_fill}; }}
.thumb {{ background: {rgba(fg, 0.05)}; border: 1px solid {line}; }}
.card-name {{ font-weight: bold; color: {fg}; }}
.tile .installed-mark {{ font-size: {t.font_px * 1.2:.1f}px; min-width: 0; }}

/* Backgrounds: theme list down the left edge */
.sidebar {{ border-right: 1px solid {line}; }}
.sidebar row {{ padding: 8px 14px; border-radius: 0; background: none; }}
.sidebar row:hover {{ background: {rgba(fg, t.hover_fill)}; }}
.sidebar row:selected {{ background: {sel_fill}; color: {t.selected_text}; }}
.sidebar row:selected label {{ color: {t.selected_text}; }}
.toolbar {{ padding: 8px 14px; border-bottom: 1px solid {line}; }}

/* colour swatches on the theme page */
.swatch {{ min-width: 30px; min-height: 30px; border: 1px solid {line}; }}
.swatch-name {{ color: {dim}; font-size: {t.font_px * 0.8:.1f}px; }}

/* fonts: the name is drawn in its own face, so leave font-family to the row */
.font-row {{ padding: 12px 18px; }}
.export-list row {{ padding: 6px 4px; background: none; border-bottom: 1px solid {line}; }}

/* Rotation: a background left out of the rotation is dimmed and greyed; picked = full colour */
.tile.off .thumb {{ filter: grayscale(100%) brightness(45%); }}
.tile.off label {{ color: {dim}; }}
.rot-grid:disabled .tile {{ opacity: 0.55; }}
/* themes left out while Themes rotation is on: greyed, and clicking them does nothing */
.sidebar row.unchecked label {{ color: {rgba(fg, 0.4)}; }}
.sidebar row.unchecked:hover {{ background: none; }}
.toolbar :disabled label {{ color: {rgba(fg, 0.35)}; }}
"""
