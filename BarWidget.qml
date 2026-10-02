import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// OmaSkins' palette in the bar. A click drops a small panel down from the icon (Omarchy's own panel, like
// its other bar widgets): Next background, Next theme, Open OmaSkins. "Next" asks OmaSkins' rotation
// engine, so a skip follows the rotation's own lists and the schedule carries on. Arrow keys and Enter work,
// Esc or a click elsewhere closes it. Drawn in the bar's and the popups' colours, so it follows every theme.
Panel {
  id: root
  moduleName: "io.github.jesuslovesyou1013.omaskins"
  manageIpc: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string pluginDir:
    decodeURIComponent(Qt.resolvedUrl(".").toString().replace(/^file:\/\//, "").replace(/\/$/, ""))
  property int cursor: -1

  readonly property var actions: [
    { icon: "󰋩", label: "Next background", command: "omaskins-rotate next background" },
    { icon: "󰏘", label: "Next theme", command: "omaskins-rotate next theme" },
    { icon: "󰏌", label: "Open OmaSkins", command: "omaskins-manager", app: true }
  ]

  function quoted(path) {
    return "'" + String(path).replace(/'/g, "'\\''") + "'"
  }

  function run(index) {
    var action = actions[index]
    if (!action || !bar) return
    var parts = action.command.split(" ")
    var path = quoted(pluginDir + "/" + parts[0]) + (parts.length > 1 ? " " + parts.slice(1).join(" ") : "")
    bar.run(action.app ? "setsid -f uwsm-app -- " + path : path)
    close()
  }

  onOpenedChanged: cursor = -1

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰏘"
    tooltipText: root.opened ? "" : "OmaSkins"
    onPressed: function(b) { root.toggle() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(220))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(300))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (dy === 0) return
        var n = root.actions.length
        root.cursor = root.cursor < 0 ? (dy > 0 ? 0 : n - 1) : (root.cursor + dy + n) % n
      }
      onActivateRequested: if (root.cursor >= 0) root.run(root.cursor)
      onReturnRequested: if (root.cursor >= 0) root.run(root.cursor)
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(2)

        Repeater {
          model: root.actions

          Button {
            required property var modelData
            required property int index
            width: column.width
            leftAlign: true
            iconText: modelData.icon
            text: modelData.label
            foreground: Color.popups.text
            fontFamily: root.fontFamily
            hasCursor: root.cursor === index
            onHovered: function(isHovered) { if (isHovered) root.cursor = index }
            onClicked: root.run(index)
          }
        }
      }
    }
  }
}
