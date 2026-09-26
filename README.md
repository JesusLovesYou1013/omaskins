# OmaSkins Manager

**Prototype.** An app for [Omarchy](https://omarchy.org) to browse and manage the look of your
desktop in one place: themes, backgrounds and fonts. Also called OmaSkins for short.

> This is a UI prototype. Everything is real (the community theme list, your installed themes,
> backgrounds and fonts), but every button only shows the Omarchy command it *would* run.
> Nothing on your system is changed.

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

**Share…** shows what a zip of your current setup (or one theme) would contain. Anything listed
on omarchy.org, or available as an Arch package, travels as the same link the stock installer
uses. Everything else (a theme that isn't listed, your own backgrounds, a font that isn't from a
package) is copied into the zip, so it works for someone who has never seen it.

## Commands it would run

Every action maps to Omarchy's own commands: `omarchy-theme-install`, `omarchy-theme-set`,
`omarchy-theme-remove`, `omarchy-theme-bg-set`, `omarchy-font-set`, `omarchy-install-font`,
`omarchy-pkg-add` and `omarchy-pkg-remove`.

## Tests

    python3 -m unittest discover -s tests -v

Headless; they run against a throwaway fake home and never touch your real files.

## License

GPL-3.0
