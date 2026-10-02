import QtQuick
import Quickshell
import qs.Ui

// OmaSkins' palette in the bar: a click opens OmaSkins' menu (Omarchy's own menu) to skip to the next
// theme or background, or to open OmaSkins. The icon is drawn like Omarchy's own bar icons, in the bar's
// colours, so it follows every theme.
BarWidget {
  id: root
  moduleName: "io.github.jesuslovesyou1013.omaskins"

  readonly property string menuPath:
    decodeURIComponent(Qt.resolvedUrl("omaskins-menu").toString().replace(/^file:\/\//, ""))

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰏘"
    tooltipText: "OmaSkins"
    onPressed: function(b) {
      if (root.bar) root.bar.run("'" + root.menuPath.replace(/'/g, "'\\''") + "'")
    }
  }
}
