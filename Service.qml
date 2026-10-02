import QtQuick
import Quickshell
import Quickshell.Io

// OmaSkins' rotation engine, running while this plugin is enabled, OmaSkins open or not.
// All logic lives in omaskins-rotate (Python); this file only keeps it running, the way
// Omarchy's clipboard plugin keeps its watchers: pdeathsig ends the engine whenever the
// shell exits, and a crashed engine is restarted after a pause. The engine also stops by
// itself once the plugin is disabled (it checks shell.json). -B: Python writes no
// __pycache__ here, since any file change in a plugin folder makes the shell reload it.
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

  Component.onDestruction: engine.running = false
}
