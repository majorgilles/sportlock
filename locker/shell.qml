// sportlock locker: holds the Wayland session lock while the service says so.
//
// Reads $SPORTLOCK_STATE every 500 ms. Releases the lock when `locked` turns false or the file
// goes stale (service stopped or crashed): sportlock fails open, it never holds the machine
// longer than the service allows.
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
  id: root

  readonly property string statePath: Quickshell.env("SPORTLOCK_STATE")
  readonly property string cli: Quickshell.env("SPORTLOCK_BIN") || "sportlock"
  readonly property int staleMs: 15000

  property var st: null
  property double nowMs: Date.now()
  property bool releasing: false
  property var ticks: ({})
  property bool overrideOpen: false
  property string message: ""

  readonly property var theme: st && st.theme ? st.theme : ({})
  readonly property color bg: theme.background || "#121212"
  readonly property color fg: theme.foreground || "#bebebe"
  readonly property color accent: theme.accent || "#e68e0d"
  readonly property color muted: theme.muted || "#555555"
  readonly property color urgent: theme.urgent || "#d35f5f"

  readonly property bool fresh: st !== null && nowMs - st.updated_at < staleMs
  readonly property bool wantLocked: fresh && st.locked === true
  readonly property var items: st && st.session ? st.session.items : []
  readonly property bool allTicked: {
    for (var i = 0; i < items.length; i++) if (!ticks[i]) return false
    return items.length > 0
  }
  readonly property double unlockAt: {
    if (!st || !st.lock) return 0
    var end = st.lock.end
    if (st.override && st.override.unlock_at < end) end = st.override.unlock_at
    return end
  }

  function clock(ms) {
    var s = Math.max(0, Math.ceil(ms / 1000))
    var m = Math.floor(s / 60)
    s = s % 60
    return m + ":" + (s < 10 ? "0" : "") + s
  }

  function toggle(i) {
    var next = Object.assign({}, ticks)
    next[i] = !next[i]
    ticks = next
  }

  function run(args) {
    if (cmd.running) return
    message = ""
    cmd.command = [cli].concat(args)
    cmd.running = true
  }

  function readState() {
    stateFile.reload()
    try {
      st = JSON.parse(stateFile.text())
    } catch (e) {
      // Missing or half-written file: keep the last state; staleness releases eventually.
    }
    nowMs = Date.now()

    if (!lock.locked && !releasing && wantLocked) lock.locked = true
    if (lock.locked && !wantLocked) release()
    if (!lock.locked && !wantLocked && !quitTimer.running) quitTimer.start()
  }

  function release() {
    releasing = true
    lock.locked = false
    if (!quitTimer.running) quitTimer.start()
  }

  FileView {
    id: stateFile
    path: root.statePath
    blockLoading: true
    printErrors: false
  }

  Timer {
    interval: 500
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.readState()
  }

  Timer {
    id: quitTimer
    interval: 1500
    onTriggered: if (!lock.locked) Qt.quit()
  }

  Process {
    id: cmd
    stdout: StdioCollector { id: cmdOut }
    stderr: StdioCollector { id: cmdErr }
    onExited: function(exitCode) {
      if (exitCode !== 0) root.message = String(cmdErr.text || cmdOut.text).trim().replace(/^sportlock: /, "")
      else root.overrideOpen = false
    }
  }

  component Btn: Rectangle {
    id: btn
    property string label: ""
    property bool enabled: true
    property bool primary: false
    signal clicked()
    implicitWidth: btnText.implicitWidth + 40
    implicitHeight: 44
    radius: 6
    color: !enabled ? "transparent" : (primary ? root.accent : (area.containsMouse ? Qt.rgba(1, 1, 1, 0.08) : "transparent"))
    border.width: 1
    border.color: enabled ? (primary ? root.accent : root.muted) : Qt.rgba(1, 1, 1, 0.1)
    Text {
      id: btnText
      anchors.centerIn: parent
      text: btn.label
      font.pixelSize: 16
      color: !btn.enabled ? root.muted : (btn.primary ? root.bg : root.fg)
    }
    MouseArea {
      id: area
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: btn.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
      onClicked: if (btn.enabled) btn.clicked()
    }
  }

  WlSessionLock {
    id: lock
    locked: false

    WlSessionLockSurface {
      color: root.bg

      Column {
        anchors.centerIn: parent
        width: Math.min(parent.width - 64, 560)
        spacing: 28

        Column {
          width: parent.width
          spacing: 6
          Text {
            text: root.st && root.st.lock && root.st.lock.test ? "Test lock" : "Time to train"
            color: root.fg
            font.pixelSize: 44
            font.weight: Font.DemiBold
          }
          Text {
            text: (root.st && root.st.override ? "Override: unlocking in " : "Unlocks in ")
                  + root.clock(root.unlockAt - root.nowMs) + " — or when you finish the session"
            color: root.st && root.st.override ? root.urgent : root.muted
            font.pixelSize: 18
          }
        }

        Rectangle {
          width: parent.width
          height: sessionColumn.implicitHeight + 40
          radius: 10
          color: Qt.rgba(1, 1, 1, 0.04)
          border.width: 1
          border.color: Qt.rgba(1, 1, 1, 0.08)

          Column {
            id: sessionColumn
            x: 20; y: 20
            width: parent.width - 40
            spacing: 4

            Text {
              text: root.st && root.st.session ? root.st.session.title : ""
              color: root.accent
              font.pixelSize: 15
              font.capitalization: Font.AllUppercase
              font.letterSpacing: 1
              bottomPadding: 8
            }

            Repeater {
              model: root.items
              delegate: Rectangle {
                required property var modelData
                required property int index
                width: sessionColumn.width
                height: 40
                radius: 6
                color: rowArea.containsMouse ? Qt.rgba(1, 1, 1, 0.05) : "transparent"
                Row {
                  anchors.verticalCenter: parent.verticalCenter
                  x: 10
                  spacing: 14
                  Rectangle {
                    width: 20; height: 20; radius: 4
                    anchors.verticalCenter: parent.verticalCenter
                    color: root.ticks[index] ? root.accent : "transparent"
                    border.width: 1
                    border.color: root.ticks[index] ? root.accent : root.muted
                    Text { anchors.centerIn: parent; text: "✓"; visible: root.ticks[index]; color: root.bg; font.pixelSize: 14 }
                  }
                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData
                    color: root.ticks[index] ? root.muted : root.fg
                    font.pixelSize: 17
                    font.strikeout: root.ticks[index] === true
                  }
                }
                MouseArea {
                  id: rowArea
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.toggle(index)
                }
              }
            }
          }
        }

        Row {
          spacing: 12
          Btn {
            label: "Finish session"
            primary: true
            enabled: root.allTicked
            onClicked: root.run(["complete"])
          }
          Btn {
            visible: root.st && root.st.lock && root.st.lock.overridable && !root.st.override
            label: root.overrideOpen ? "Never mind" : "Override…"
            onClicked: { root.overrideOpen = !root.overrideOpen; root.message = "" }
          }
          Btn {
            visible: root.st && root.st.override !== null && root.st.override !== undefined
            label: "Cancel override"
            onClicked: root.run(["cancel-override"])
          }
        }

        Column {
          visible: root.overrideOpen && root.st && !root.st.override
          width: parent.width
          spacing: 10
          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Type this to start a " + Math.round((root.st ? root.st.override_wait_seconds : 300) / 60)
                  + "-minute countdown:\n“" + (root.st ? root.st.override_phrase : "") + "”"
            color: root.muted
            font.pixelSize: 15
          }
          Rectangle {
            width: parent.width
            height: 44
            radius: 6
            color: "transparent"
            border.width: 1
            border.color: phrase.activeFocus ? root.accent : root.muted
            TextInput {
              id: phrase
              anchors.fill: parent
              anchors.margins: 12
              verticalAlignment: TextInput.AlignVCenter
              color: root.fg
              font.pixelSize: 16
              focus: root.overrideOpen
              onAccepted: { root.run(["override"].concat(text.split(/\s+/).filter(function(w) { return w.length > 0 }))); text = "" }
            }
          }
        }

        Text {
          visible: root.message.length > 0
          text: root.message
          color: root.urgent
          font.pixelSize: 15
        }
      }
    }
  }
}
