// sportlock warning popup: shown in front of everything when a lock is coming up, so the warning
// can't be missed. Dismissed with Enter, Esc, the button or a click anywhere (after a short grace
// period so a keystroke already under way doesn't close it unread). Closes itself when the lock
// starts. The service passes what to show as JSON in $SPORTLOCK_POPUP. Exercises replaced after
// the last session can be answered here (agree, or keep the old one) through `sportlock raw`.
import QtQuick
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
  id: root

  readonly property var content: {
    try { return JSON.parse(Quickshell.env("SPORTLOCK_POPUP") || "{}") } catch (e) { return {} }
  }
  readonly property var theme: content.theme || {}
  readonly property color bg: theme.background || "#121212"
  readonly property color fg: theme.foreground || "#bebebe"
  readonly property color accent: theme.accent || "#e68e0d"
  readonly property color muted: theme.muted || "#555555"
  readonly property color line: Qt.rgba(1, 1, 1, 0.09)

  property double nowMs: Date.now()
  property bool armed: false
  readonly property string cli: Quickshell.env("SPORTLOCK_BIN") || "sportlock"
  // Answers given here, by chain: {agree, kept} once saved, {error} when the service refused.
  property var answers: ({})
  property string answering: ""

  function answer(change, agree, note) {
    if (answering !== "") return
    answering = change.chain
    answerCmd.agree = agree
    answerCmd.change = change
    answerCmd.command = [cli, "raw", JSON.stringify({ cmd: "replacement-answer", chain: change.chain, agree: agree, note: note })]
    answerCmd.running = true
  }

  Process {
    id: answerCmd
    property bool agree: false
    property var change: null
    stdout: StdioCollector { id: answerOut }
    stderr: StdioCollector { id: answerErr }
    onExited: function(exitCode) {
      var next = Object.assign({}, root.answers)
      next[change.chain] = exitCode === 0 ? { agree: agree, kept: change.from_name }
                                          : { error: String(answerErr.text || answerOut.text).trim().replace(/^sportlock: /, "") }
      root.answers = next
      root.answering = ""
    }
  }
  readonly property double leftMs: (content.start || 0) - nowMs

  function dismiss() { if (armed) Qt.quit() }

  function countdown() {
    var s = Math.max(0, Math.round(leftMs / 1000))
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0")
  }

  Timer {
    interval: 500; running: true; repeat: true
    onTriggered: {
      root.nowMs = Date.now()
      if (root.content.start && root.leftMs <= 0) Qt.quit()  // the lock screen takes over
    }
  }
  Timer { interval: 800; running: true; onTriggered: root.armed = true }

  Variants {
    model: Quickshell.screens

    PanelWindow {
      id: panel
      required property var modelData
      readonly property bool focused: !Hyprland.focusedMonitor || Hyprland.focusedMonitor.name === modelData.name

      screen: modelData
      color: Qt.rgba(0, 0, 0, 0.6)
      exclusionMode: ExclusionMode.Ignore
      anchors { top: true; bottom: true; left: true; right: true }
      WlrLayershell.namespace: "sportlock-warning"
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.keyboardFocus: focused ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None

      MouseArea { anchors.fill: parent; onClicked: root.dismiss() }

      Item {
        id: keyCatcher
        anchors.fill: parent
        focus: true
        Keys.onPressed: function(event) {
          if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Escape) {
            root.dismiss()
            event.accepted = true
          }
        }
      }

      Rectangle {
        anchors.centerIn: parent
        width: Math.min(parent.width - 64, 620)
        height: card.implicitHeight + 64
        radius: 14
        color: root.bg
        border.width: 1
        border.color: root.accent

        Column {
          id: card
          x: 32; y: 32
          width: parent.width - 64
          spacing: 14

          Text {
            text: root.content.headline || "Training lock"
            color: root.fg
            font.pixelSize: 28
            font.weight: Font.DemiBold
          }
          Text {
            width: card.width
            wrapMode: Text.WordWrap
            text: "Your desktop locks in " + root.countdown() + (root.content.minutes ? " for " + root.content.minutes + " min" : "")
                  + (root.content.title ? "  ·  " + root.content.title : "")
            color: root.accent
            font.pixelSize: 17
          }
          Text {
            visible: !!root.content.reason
            width: card.width
            wrapMode: Text.WordWrap
            text: root.content.reason || ""
            color: root.muted
            font.pixelSize: 15
          }

          Column {
            visible: (root.content.recommendations || []).length > 0
            width: card.width
            spacing: 8
            topPadding: 6
            Text { text: "Coach, on your last session"; color: root.accent; font.pixelSize: 13; font.capitalization: Font.AllUppercase; font.letterSpacing: 1 }
            Repeater {
              model: root.content.recommendations || []
              delegate: Text {
                required property var modelData
                width: card.width
                wrapMode: Text.WordWrap
                textFormat: Text.StyledText
                text: "•  " + (modelData.about ? "<b>" + modelData.about.replace(/</g, "&lt;") + ":</b> " : "")
                      + modelData.advice.replace(/</g, "&lt;")
                color: root.fg
                font.pixelSize: 15
              }
            }
          }

          Column {
            visible: (root.content.replacements || []).length > 0
            width: card.width
            spacing: 12
            topPadding: 6
            Text { text: "Changed after your last session"; color: root.accent; font.pixelSize: 13; font.capitalization: Font.AllUppercase; font.letterSpacing: 1 }
            Repeater {
              model: root.content.replacements || []
              delegate: Column {
                id: change
                required property var modelData
                readonly property var given: root.answers[modelData.chain] || null
                readonly property bool done: given !== null && !given.error
                width: card.width
                spacing: 8
                Text {
                  width: card.width
                  wrapMode: Text.WordWrap
                  textFormat: Text.StyledText
                  text: "<b>" + change.modelData.to_name.replace(/</g, "&lt;") + "</b> replaces "
                        + change.modelData.from_name.replace(/</g, "&lt;")
                        + (change.modelData.by === "coach" ? " (the coach's choice)" : "")
                  color: root.fg
                  font.pixelSize: 16
                }
                Text {
                  visible: !!change.modelData.reason
                  width: card.width
                  wrapMode: Text.WordWrap
                  text: change.modelData.reason || ""
                  color: root.muted
                  font.pixelSize: 14
                }
                Rectangle {
                  visible: !change.done
                  width: card.width
                  height: 38
                  radius: 6
                  color: "transparent"
                  border.width: 1
                  border.color: why.activeFocus ? root.accent : root.muted
                  TextInput {
                    id: why
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    verticalAlignment: TextInput.AlignVCenter
                    color: root.fg
                    font.pixelSize: 15
                    clip: true
                    Keys.onEscapePressed: keyCatcher.forceActiveFocus()
                  }
                  Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    visible: why.text.length === 0
                    text: "tell the coach why (optional)"
                    color: root.muted
                    font.pixelSize: 14
                  }
                }
                Row {
                  visible: !change.done
                  spacing: 10
                  Rectangle {
                    width: agreeText.implicitWidth + 32
                    height: 36
                    radius: 18
                    color: "transparent"
                    border.width: 1
                    border.color: root.accent
                    Text { id: agreeText; anchors.centerIn: parent; text: "Agree"; color: root.fg; font.pixelSize: 15 }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.answer(change.modelData, true, why.text) }
                  }
                  Rectangle {
                    width: keepText.implicitWidth + 32
                    height: 36
                    radius: 18
                    color: "transparent"
                    border.width: 1
                    border.color: root.muted
                    Text { id: keepText; anchors.centerIn: parent; text: "Keep " + change.modelData.from_name; color: root.fg; font.pixelSize: 15 }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.answer(change.modelData, false, why.text) }
                  }
                }
                Text {
                  visible: change.given !== null
                  width: card.width
                  wrapMode: Text.WordWrap
                  text: !change.given ? "" : change.given.error ? change.given.error
                        : change.given.agree ? "✓ Agreed: " + change.modelData.to_name + " it is."
                        : "✓ Kept " + change.given.kept + ": it's back in your next session, and the coach will know."
                  color: change.given && change.given.error ? (root.theme.urgent || "#d35f5f") : root.accent
                  font.pixelSize: 14
                }
              }
            }
          }

          Row {
            spacing: 16
            topPadding: 8
            Rectangle {
              width: okText.implicitWidth + 40
              height: 42
              radius: 21
              color: okArea.containsMouse ? Qt.lighter(root.accent, 1.15) : root.accent
              Text { id: okText; anchors.centerIn: parent; text: "Got it"; color: root.bg; font.pixelSize: 16; font.weight: Font.DemiBold }
              MouseArea { id: okArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.dismiss() }
            }
            Text {
              height: 42
              verticalAlignment: Text.AlignVCenter
              text: "Enter or Esc to close"
              color: root.muted
              font.pixelSize: 13
            }
          }
        }

        MouseArea { anchors.fill: parent; z: -1 }  // clicks on the card itself don't dismiss
      }
    }
  }
}
