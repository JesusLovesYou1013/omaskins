# OmaSkins Manager

**In development.** An app for [Omarchy](https://omarchy.org) to browse and manage the look of
your desktop in one place: themes, backgrounds and fonts, a rotation that changes them on a
schedule, window transparency, and Qt apps and file dialogs that follow the theme. Also called
OmaSkins for short.

> Everything works and changes your system through Omarchy's own commands. It isn't packaged as an
> installable Omarchy plugin yet; that's the next step.

**Requirements:** Python 3 with PyGObject, GTK4 and libadwaita, plus ImageMagick for thumbnails.
All of these ship with a stock Omarchy install.

    ./omaskins-manager

## Tabs

- **Themes**
  - **Browse**: every community theme listed on [omarchy.org/themes](https://omarchy.org/themes/), with
    its screenshot. Ones you already have carry an accent ✓.
  - **Installed**: the built-in themes plus the ones you've added. The current theme comes first.
  - Click a theme for its page: large screenshot, its color palette (read from the theme's
    `colors.toml`, fetched from GitHub for themes you haven't installed), details, and
    **Add / Apply / Remove / Backgrounds / Share / Open on GitHub**.
- **Backgrounds**: your installed themes down the left edge. Pick one to see its backgrounds:
  the ones that ship with the theme, plus your own from `~/.config/omarchy/backgrounds/<theme>/`
  (marked **Yours**). The current background carries the ✓.
- **Fonts**
  - **Browse**: the Nerd Font packages in the Arch repos, which is where Omarchy's own
    Install › Font gets them. The ones Omarchy's menu offers are marked **Omarchy pick**.
  - **Installed**: the monospace fonts Omarchy can use, each name drawn in its own font. The
    current font carries the ✓. Previews are sized so every font's lowercase letters are the
    same height as your current font's, so they compare fairly.

## Share

**Export all settings…** saves your setup as a `.omaskins` file; double-click one to import it.
Anything listed on omarchy.org, or available as an Arch package, travels as the same link the
stock installer uses. Everything else (a theme that isn't listed, your own backgrounds, a font
that isn't from a package) is copied into the file, so it works for someone who has never seen
it. On import you tick what to bring in; names that already exist are merged, never copied. Any
work that needs your password (fonts, built-in themes) is done at once, in Omarchy's own
installer window, and can be cancelled.

## Rotation

Changes theme and background on a schedule, with separate day and night sets, and keeps running
with the app closed (OmaSkins' engine runs as an Omarchy service plugin). It pauses while the
screen is locked or the screensaver runs.

## Transparency, Qt apps and file dialogs

All of this runs as you: no password, no packages, nothing of Omarchy's changed. It's set on the
Themes tab (**Transparency** slider, **Qt apps** switch) and kept up to date by OmaSkins' engine,
for every theme you switch to or rotate through.

- **Transparency**: five steps; step 2 is exactly Omarchy's own. Windows fade live (no Hyprland
  reload). Nautilus and the file dialogs make only their background see-through (files, icons and
  text stay solid), in libadwaita's grey, with the sidebar a little more solid.
- **Qt apps** (on by default): Qt5 apps such as VLC and KeePassXC in the theme's colours,
  see-through like the rest (a video stays solid). OmaSkins builds its small Qt style on your
  machine the first time a Qt5 app is installed, with the compiler Omarchy already ships
  (`base-devel`) and Qt5's own build kit. Apps opened after your next login use it. Apps with a
  style of their own (KeePassXC's dark/light themes) keep it.
- **File dialogs**: Open/Save dialogs (Omarchy's dialog service, GTK3) look like Nautilus. A
  change shows from the next dialog you open. Dark themes use GTK3's dark theme from
  `gnome-themes-extra`, which is part of Omarchy's base install; if it's missing, dark-theme
  dialogs are left as they are.

**What it writes:** `~/.config/hypr/omaskins.lua` (and one line loading it at the end of
`~/.config/hypr/looknfeel.lua`), an OmaSkins block in `~/.config/gtk-4.0/gtk.css` and
`~/.config/gtk-3.0/gtk.css` (anything else in those files is left alone),
`~/.config/omaskins/`, and the built Qt style in `~/.local/share/omaskins/qt5/`.

**To undo:** set Transparency back to step 2 and switch **Qt apps** off; the blocks and the Qt
style are removed. Removing OmaSkins turns the Qt style off at your next login.

## Commands it runs

Every action uses Omarchy's own commands: `omarchy-theme-install`, `omarchy-theme-set`,
`omarchy-theme-remove`, `omarchy-theme-bg-set`, `omarchy-font-set`, `omarchy-install-font`,
`omarchy-pkg-add` and `omarchy-pkg-remove`.

## Tests

    python3 -m unittest discover -s tests -v

Headless; they run against a throwaway fake home and never touch your real files.

## License

GPL-3.0
