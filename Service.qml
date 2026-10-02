import QtQuick
import Quickshell
import Quickshell.Io

// OmaSkins' rotation engine, running while this plugin is enabled, OmaSkins open or not.
// All logic lives in omaskins-rotate (Python); this file only keeps it running, the way
// Omarchy's clipboard plugin keeps its watchers: pdeathsig ends the engine whenever the
// shell exits, and a crashed engine is restarted after a pause. The engine also stops by
// itself once the plugin is disabled (it checks shell.json). -B: Python writes no
// __pycache__ here, since any file change in a plugin folder makes the shell reload it.
// While on, the engine also puts OmaSkins in Omarchy's menu (Style ›
// OmaSkins); turning the plugin off takes them away again (removeCommand below).
Item {
  id: root

  readonly property string pluginId: "io.github.jesuslovesyou1013.omaskins"
  readonly property string enginePath:
    decodeURIComponent(Qt.resolvedUrl("omaskins-rotate").toString().replace(/^file:\/\//, ""))

  Process {
    id: engine
    command: ["setpriv", "--pdeathsig", "TERM", "python3", "-B", root.enginePath, "run", "--plugin-id", root.pluginId]
    running: true
    onExited: restartTimer.restart()
    stderr: StdioCollector {
      onStreamFinished: if (text.trim() !== "") console.warn("omaskins-rotate: " + text.trim())
    }
  }

  Timer {
    id: restartTimer
    interval: 30000
    onTriggered: engine.running = true
  }

  // Turned off or removed: OmaSkins leaves Omarchy's menu (Style › OmaSkins), and its hidden launcher
  // entry, the .omaskins file type, its copy of GTK3's dark theme (if it wrote one) and its transparency
  // for Omarchy's menus and panels (shell.toml) go too. Inline, not in a script, because `omarchy plugin remove` deletes this folder
  // right after disabling it (CtrlZ Guard's way). The service is also destroyed on a normal shell exit or
  // reload; while the id is still in shell.json (as a plugin or its bar icon) this does nothing.
  readonly property string removeCommand:
    "sleep 1; c=\"${XDG_CONFIG_HOME:-$HOME/.config}\"; d=\"${XDG_DATA_HOME:-$HOME/.local/share}\"; " +
    "grep -qF '\"" + pluginId + "\"' \"$c/omarchy/shell.json\" 2>/dev/null && exit 0; " +
    "s=\"$c/omarchy/shell.toml\"; " +
    "[ -f \"$s\" ] && sed -i --follow-symlinks '/^# >>> OmaSkins: menus, panels/,/^# <<< OmaSkins/d' \"$s\"; " +
    "f=\"$c/omarchy/extensions/omarchy-menu.jsonc\"; " +
    "[ -f \"$f\" ] && sed -i --follow-symlinks '/^  \\/\\/ >>> OmaSkins/,/^  \\/\\/ <<< OmaSkins/d' \"$f\"; " +
    "rm -f \"$d/applications/" + pluginId + ".desktop\" \"$d/mime/packages/" + pluginId + ".xml\"; " +
    "update-mime-database \"$d/mime\" >/dev/null 2>&1; " +
    "t=\"$d/themes/Adwaita-dark/gtk-3.0/gtk.css\"; " +
    "grep -qF 'Written by OmaSkins' \"$t\" 2>/dev/null && rm -f \"$t\" && " +
    "rmdir \"$d/themes/Adwaita-dark/gtk-3.0\" \"$d/themes/Adwaita-dark\" 2>/dev/null; " +
    "m=\"$c/mimeapps.list\"; " +
    "[ -f \"$m\" ] && sed -i --follow-symlinks '/^application\\/x-omaskins-setup=" +
    pluginId.replace(/\./g, "\\.") + "\\.desktop;\\?$/d' \"$m\"; true"

  Component.onDestruction: {
    engine.running = false
    Quickshell.execDetached(["bash", "-c", root.removeCommand])
  }
}
