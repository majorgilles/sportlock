// sportlock locker: holds the Wayland session lock while the service says so, and runs the
// training session on it.
//
// Reads $SPORTLOCK_STATE every 250 ms. Releases the lock when `locked` turns false or the file
// goes stale (service stopped or crashed): sportlock fails open, it never holds the machine
// longer than the service allows. All training state lives in the service; this file only
// renders it and sends actions through `sportlock raw`.
import QtQuick
import QtMultimedia
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
  property bool overrideOpen: false
  property string message: ""
  property var queue: []

  readonly property var theme: st && st.theme ? st.theme : ({})
  readonly property color bg: theme.background || "#121212"
  readonly property color fg: theme.foreground || "#bebebe"
  readonly property color accent: theme.accent || "#e68e0d"
  readonly property color muted: theme.muted || "#555555"
  readonly property color urgent: theme.urgent || "#d35f5f"
  readonly property color panel: Qt.rgba(1, 1, 1, 0.04)
  readonly property color line: Qt.rgba(1, 1, 1, 0.09)

  readonly property bool fresh: st !== null && nowMs - st.updated_at < staleMs
  readonly property bool wantLocked: fresh && st.locked === true

  readonly property var tr: st && st.training ? st.training : null
  readonly property string phase: tr ? tr.phase : ""
  readonly property var ex: tr && tr.current < tr.exercises.length ? tr.exercises[tr.current] : null
  readonly property int setNo: ex ? ex.sets.length + 1 : 0
  readonly property double elapsedMs: tr && tr.set_started_at && phase === "running" ? nowMs - tr.set_started_at : 0
  readonly property double restLeftMs: tr && tr.rest_until && phase === "resting" ? tr.rest_until - nowMs : 0
  readonly property double unlockAt: {
    if (!st || !st.lock) return 0
    var end = st.lock.end
    if (st.override && st.override.unlock_at < end) end = st.override.unlock_at
    return end
  }

  // -- helpers -----------------------------------------------------------------------------

  function clock(ms) {
    var s = Math.max(0, Math.floor(ms / 1000))
    var m = Math.floor(s / 60)
    s = s % 60
    return m + ":" + (s < 10 ? "0" : "") + s
  }

  function targetText(e) {
    if (!e) return ""
    var t = e.target
    if (e.kind === "timed") return clock(t.seconds * 1000)
    var setsText = t.sets + (t.sets === 1 ? " set" : " sets")
    var work = e.kind === "reps" ? t.reps[0] + "–" + t.reps[1] + " reps" : "hold " + t.seconds + " s"
    return setsText + " × " + work + (t.rest ? " · rest " + t.rest + " s" : "")
  }

  function setText(s, e) {
    var parts = []
    if (s.reps !== null && s.reps !== undefined) parts.push(s.reps + " reps")
    parts.push(clock(s.seconds * 1000))
    if (s.load_kg) parts.push("+" + s.load_kg + " kg")
    return parts.join(" · ")
  }

  // Target duration for holds and timed blocks, in ms (0 for rep sets).
  readonly property double targetMs: ex && ex.kind !== "reps" ? ex.target.seconds * 1000 : 0

  function send(payload) {
    var next = queue.slice()
    next.push(payload)
    queue = next
    pump()
  }

  function train(action, args) {
    var payload = Object.assign({ cmd: "train", action: action }, args || {})
    send(payload)
  }

  function pump() {
    if (cmd.running || queue.length === 0) return
    var payload = queue[0]
    queue = queue.slice(1)
    message = ""
    cmd.command = [cli, "raw", JSON.stringify(payload)]
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

    if (preview) return
    if (!lock.locked && !releasing && wantLocked) lock.locked = true
    if (lock.locked && !wantLocked) release()
    if (!lock.locked && !wantLocked && !quitTimer.running) quitTimer.start()
  }

  function release() {
    releasing = true
    lock.locked = false
    if (!quitTimer.running) quitTimer.start()
  }

  // -- plumbing ----------------------------------------------------------------------------

  FileView {
    id: stateFile
    path: root.statePath
    blockLoading: true
    printErrors: false
  }

  Timer {
    interval: 250
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
      root.readState()
      root.pump()
    }
  }

  // -- sound: a tick every second while a set runs ------------------------------------------

  SoundEffect { id: tick; source: Qt.resolvedUrl("sounds/tick.wav"); volume: 0.7 }
  SoundEffect { id: tock; source: Qt.resolvedUrl("sounds/tock.wav"); volume: 0.8 }
  SoundEffect { id: ding; source: Qt.resolvedUrl("sounds/ding.wav"); volume: 0.8 }

  property int lastSecond: -1
  property bool targetDinged: false
  property bool restDinged: false

  onPhaseChanged: { lastSecond = -1; targetDinged = false; restDinged = false }

  Timer {
    interval: 50
    repeat: true
    running: root.wantLocked && (root.phase === "running" || root.phase === "resting")
    onTriggered: {
      root.nowMs = Date.now()
      if (root.phase === "running") {
        var second = Math.floor(root.elapsedMs / 1000)
        if (second !== root.lastSecond && second > 0) {
          if (second % 10 === 0) tock.play(); else tick.play()
        }
        root.lastSecond = second
        if (root.targetMs > 0 && root.elapsedMs >= root.targetMs && !root.targetDinged) {
          root.targetDinged = true
          ding.play()
        }
      } else {
        var left = Math.ceil(root.restLeftMs / 1000)
        if (left !== root.lastSecond && left > 0 && left <= 5) tick.play()
        root.lastSecond = left
        if (root.restLeftMs <= 0 && root.tr && root.tr.rest_until && !root.restDinged) {
          root.restDinged = true
          ding.play()
        }
      }
    }
  }

  // -- building blocks ---------------------------------------------------------------------

  component Btn: Rectangle {
    id: btn
    property string label: ""
    property string hint: ""
    property bool enabled: true
    property bool primary: false
    property bool big: false
    signal clicked()
    implicitWidth: btnRow.implicitWidth + (big ? 56 : 36)
    implicitHeight: big ? 60 : 42
    radius: 6
    color: !enabled ? "transparent" : (primary ? root.accent : (area.containsMouse ? Qt.rgba(1, 1, 1, 0.08) : "transparent"))
    border.width: 1
    border.color: enabled ? (primary ? root.accent : root.muted) : Qt.rgba(1, 1, 1, 0.1)
    Row {
      id: btnRow
      anchors.centerIn: parent
      spacing: 10
      Text {
        text: btn.label
        font.pixelSize: btn.big ? 22 : 16
        font.weight: btn.primary ? Font.DemiBold : Font.Normal
        color: !btn.enabled ? root.muted : (btn.primary ? root.bg : root.fg)
      }
      Text {
        visible: btn.hint.length > 0
        anchors.verticalCenter: parent.verticalCenter
        text: btn.hint
        font.pixelSize: 12
        color: btn.primary ? Qt.darker(root.bg, 0.6) : root.muted
      }
    }
    MouseArea {
      id: area
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: btn.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
      onClicked: if (btn.enabled) btn.clicked()
    }
  }

  component Field: Rectangle {
    id: field
    property alias text: input.text
    property alias input: input
    property string placeholder: ""
    property string suffix: ""
    signal accepted()
    implicitWidth: 160
    implicitHeight: 46
    radius: 6
    color: "transparent"
    border.width: 1
    border.color: input.activeFocus ? root.accent : root.muted
    TextInput {
      id: input
      anchors.fill: parent
      anchors.leftMargin: 12
      anchors.rightMargin: suffixText.implicitWidth + 16
      verticalAlignment: TextInput.AlignVCenter
      color: root.fg
      font.pixelSize: 18
      clip: true
      onAccepted: field.accepted()
    }
    Text {
      anchors.left: parent.left
      anchors.leftMargin: 12
      anchors.verticalCenter: parent.verticalCenter
      visible: input.text.length === 0
      text: field.placeholder
      color: root.muted
      font.pixelSize: 16
    }
    Text {
      id: suffixText
      anchors.right: parent.right
      anchors.rightMargin: 12
      anchors.verticalCenter: parent.verticalCenter
      text: field.suffix
      color: root.muted
      font.pixelSize: 14
    }
  }

  // Multi-line notes: grows with its content; Enter submits, Shift+Enter starts a new line.
  component NotesField: Rectangle {
    id: notes
    property alias text: edit.text
    property alias input: edit
    property string placeholder: ""
    property int minHeight: 110
    signal accepted()
    implicitHeight: Math.min(320, Math.max(minHeight, edit.contentHeight + 26))
    radius: 6
    color: "transparent"
    border.width: 1
    border.color: edit.activeFocus ? root.accent : root.muted
    clip: true
    Flickable {
      id: flick
      anchors.fill: parent
      anchors.margins: 13
      contentHeight: edit.contentHeight
      boundsBehavior: Flickable.StopAtBounds
      TextEdit {
        id: edit
        width: flick.width
        wrapMode: TextEdit.Wrap
        color: root.fg
        selectionColor: root.accent
        font.pixelSize: 16
        onCursorRectangleChanged: {
          if (cursorRectangle.y < flick.contentY) flick.contentY = cursorRectangle.y
          else if (cursorRectangle.y + cursorRectangle.height > flick.contentY + flick.height)
            flick.contentY = cursorRectangle.y + cursorRectangle.height - flick.height
        }
        Keys.onPressed: function(event) {
          if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && !(event.modifiers & Qt.ShiftModifier)) {
            notes.accepted()
            event.accepted = true
          }
        }
      }
    }
    Text {
      anchors.left: parent.left
      anchors.top: parent.top
      anchors.margins: 13
      visible: edit.text.length === 0
      text: notes.placeholder
      color: root.muted
      font.pixelSize: 16
    }
    Text {
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      anchors.margins: 8
      visible: edit.activeFocus
      text: "Shift+Enter: new line"
      color: root.muted
      font.pixelSize: 11
    }
  }

  // Titled bullet (or numbered) list for the instructions panel.
  component InfoList: Column {
    id: info
    property string title: ""
    property var items: []
    property bool numbered: false
    width: parent ? parent.width : 300
    spacing: 6
    Text {
      text: info.title
      color: root.accent
      font.pixelSize: 13
      font.capitalization: Font.AllUppercase
      font.letterSpacing: 1
    }
    Repeater {
      model: info.items
      delegate: Text {
        required property var modelData
        required property int index
        width: info.width
        wrapMode: Text.WordWrap
        text: (info.numbered ? (index + 1) + ".  " : "•  ") + modelData
        color: root.fg
        font.pixelSize: 15
        lineHeight: 1.15
      }
    }
  }

  // 1–10 effort picker; keys 1–9 and 0 (= 10) also work while it is shown.
  component RpePicker: Row {
    id: picker
    property int value: 0
    spacing: 6
    Repeater {
      model: 10
      delegate: Rectangle {
        required property int index
        readonly property int n: index + 1
        width: 44; height: 44; radius: 6
        color: picker.value === n ? root.accent : (cellArea.containsMouse ? Qt.rgba(1, 1, 1, 0.08) : "transparent")
        border.width: 1
        border.color: picker.value === n ? root.accent : root.muted
        Text { anchors.centerIn: parent; text: n; font.pixelSize: 17; color: picker.value === n ? root.bg : root.fg }
        MouseArea { id: cellArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: picker.value = n }
      }
    }
  }

  // -- the lock surface ---------------------------------------------------------------------

  component TrainingScreen: Item {
    id: surface
    anchors.fill: parent

    property bool showCues: root.preview && Quickshell.env("SPORTLOCK_SHOW_DETAILS") === "1"
    property bool skipOpen: false

    // Space / Enter drive the session when no text field has focus.
    focus: true
    Keys.onPressed: function(event) {
      if (event.key === Qt.Key_Space || event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
        if (root.phase === "ready" || root.phase === "resting") root.train("start_set")
        else if (root.phase === "running") root.train("stop_set")
        event.accepted = true
      } else if (event.key >= Qt.Key_0 && event.key <= Qt.Key_9 && (root.phase === "rating" || root.phase === "summary")) {
        var n = event.key === Qt.Key_0 ? 10 : event.key - Qt.Key_0
        if (root.phase === "rating") ratePicker.value = n; else sessionPicker.value = n
        event.accepted = true
      } else if (event.key === Qt.Key_D) {
        surface.showCues = !surface.showCues
        event.accepted = true
      }
    }

    Connections {
      target: root
      function onPhaseChanged() {
        surface.skipOpen = false
        ratePicker.value = 0
        if (root.phase === "logging" && root.ex && root.ex.kind === "reps") {
          repsField.text = ""
          repsField.input.forceActiveFocus()
        } else if (root.phase !== "summary") {
          surface.forceActiveFocus()
        }
      }
    }

    Column {
      anchors.centerIn: parent
      width: Math.min(parent.width - 64, 1180)
      spacing: 22

      // Header: session title and lock countdown
      Item {
        width: parent.width
        height: titleCol.implicitHeight
        Column {
          id: titleCol
          spacing: 4
          Text {
            text: root.st && root.st.lock && root.st.lock.test ? "Test lock" : (root.tr ? root.tr.title : "Time to train")
            color: root.fg
            font.pixelSize: 30
            font.weight: Font.DemiBold
          }
          Text {
            text: root.tr ? (root.phase === "summary" ? "All exercises done"
                  : "Exercise " + (root.tr.current + 1) + " of " + root.tr.exercises.length
                    + (root.tr.day_type ? "  ·  " + root.tr.day_type + " day" : "")) : ""
            color: root.muted
            font.pixelSize: 15
          }
        }
        Column {
          anchors.right: parent.right
          spacing: 4
          Text {
            anchors.right: parent.right
            text: root.clock(root.unlockAt - root.nowMs)
            color: root.st && root.st.override ? root.urgent : root.fg
            font.pixelSize: 30
            font.family: "monospace"
          }
          Text {
            anchors.right: parent.right
            text: root.st && root.st.override ? "override: unlocking" : "until unlock"
            color: root.st && root.st.override ? root.urgent : root.muted
            font.pixelSize: 13
          }
        }
      }

      // Progress strip
      Row {
        spacing: 6
        visible: root.tr !== null
        Repeater {
          model: root.tr ? root.tr.exercises : []
          delegate: Rectangle {
            required property var modelData
            required property int index
            readonly property bool isCurrent: root.tr && index === root.tr.current
            width: Math.max(28, (Math.min(surface.width - 64, 1180) - 6 * ((root.tr ? root.tr.exercises.length : 1) - 1)) / (root.tr ? root.tr.exercises.length : 1))
            height: 6
            radius: 3
            visible: modelData.status !== "swapped"
            color: modelData.status === "done" ? root.accent
                   : modelData.status === "skipped" ? root.urgent
                   : isCurrent ? root.fg : root.line
          }
        }
      }

      // Exercise card, with the picture and instructions beside it
      Row {
        width: parent.width
        spacing: 20
        visible: root.ex !== null

      Rectangle {
        width: parent.width - side.width - 20
        height: card.implicitHeight + 48
        radius: 10
        color: root.panel
        border.width: 1
        border.color: root.line
        visible: root.ex !== null

        Column {
          id: card
          x: 24; y: 24
          width: parent.width - 48
          spacing: 16

          Row {
            spacing: 12
            Text {
              text: root.ex ? root.ex.name : ""
              color: root.fg
              font.pixelSize: 34
              font.weight: Font.DemiBold
            }
            Rectangle {
              anchors.verticalCenter: parent.verticalCenter
              width: patternText.implicitWidth + 16; height: 24; radius: 12
              color: "transparent"; border.width: 1; border.color: root.muted
              Text { id: patternText; anchors.centerIn: parent; text: root.ex ? root.ex.pattern : ""; color: root.muted; font.pixelSize: 12 }
            }
          }

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.targetText(root.ex) + (root.ex && root.ex.target.sets > 1 ? "   —   set " + Math.min(root.setNo, root.ex.target.sets) + " of " + root.ex.target.sets : "")
            color: root.accent
            font.pixelSize: 18
          }

          Text {
            width: parent.width
            visible: root.ex !== null && !!root.ex.target.progress
            wrapMode: Text.WordWrap
            text: root.ex && root.ex.target.progress ? "Why this level: " + root.ex.target.progress : ""
            color: root.muted
            font.pixelSize: 14
          }

          Text {
            text: (surface.showCues ? "▾ Hide" : "▸ Show") + " full instructions  (D)"
            color: root.muted
            font.pixelSize: 14
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: surface.showCues = !surface.showCues }
          }

          // Sets done so far
          Flow {
            width: parent.width
            spacing: 8
            visible: root.ex && root.ex.sets.length > 0
            Repeater {
              model: root.ex ? root.ex.sets : []
              delegate: Rectangle {
                required property var modelData
                required property int index
                width: setLabel.implicitWidth + 20; height: 30; radius: 6
                color: Qt.rgba(1, 1, 1, 0.05)
                Text { id: setLabel; anchors.centerIn: parent; text: (index + 1) + ":  " + root.setText(modelData); color: root.fg; font.pixelSize: 14 }
              }
            }
          }

          // Big clock: set stopwatch, or rest countdown
          Column {
            width: parent.width
            spacing: 4
            visible: root.phase === "ready" || root.phase === "running" || root.phase === "resting"
            Text {
              anchors.horizontalCenter: parent.horizontalCenter
              text: root.phase === "running" ? root.clock(root.elapsedMs)
                    : root.phase === "resting" ? (root.restLeftMs > 0 ? root.clock(root.restLeftMs + 999) : "Go")
                    : "0:00"
              color: root.phase === "running" ? (root.targetMs > 0 && root.elapsedMs >= root.targetMs ? root.accent : root.fg)
                     : root.phase === "resting" ? (root.restLeftMs > 0 ? root.muted : root.accent) : root.line
              font.pixelSize: 96
              font.family: "monospace"
            }
            Text {
              anchors.horizontalCenter: parent.horizontalCenter
              text: root.phase === "running" ? (root.targetMs > 0 ? "target " + root.clock(root.targetMs) : "set " + root.setNo + " running")
                    : root.phase === "resting" ? "rest" : "press Start when you begin"
              color: root.muted
              font.pixelSize: 15
            }
          }

          // Logging a finished set
          Column {
            width: parent.width
            spacing: 12
            visible: root.phase === "logging"
            Text {
              text: "Set " + root.setNo + " took " + root.clock((root.tr && root.tr.pending_seconds || 0) * 1000)
              color: root.fg
              font.pixelSize: 20
            }
            Flow {
              width: parent.width
              spacing: 12
              Field {
                id: repsField
                visible: root.ex && root.ex.kind === "reps"
                placeholder: root.ex && root.ex.kind === "reps" ? "reps (" + root.ex.target.reps[0] + "–" + root.ex.target.reps[1] + ")" : ""
                input.validator: IntValidator { bottom: 0; top: 500 }
                onAccepted: saveSet.clicked()
                Keys.onTabPressed: loadField.input.forceActiveFocus()
              }
              Field {
                id: loadField
                placeholder: "added load"
                suffix: "kg"
                input.validator: DoubleValidator { bottom: 0; top: 500; decimals: 1 }
                onAccepted: saveSet.clicked()
              }
              Btn {
                id: saveSet
                label: "Save set"
                hint: "Enter"
                primary: true
                onClicked: {
                  var args = {}
                  if (repsField.visible) args.reps = parseInt(repsField.text)
                  if (loadField.text.length > 0) args.load_kg = parseFloat(loadField.text.replace(",", "."))
                  root.train("save_set", args)
                  loadField.text = ""
                }
              }
            }
          }

          // Rating the exercise
          Column {
            width: parent.width
            spacing: 12
            visible: root.phase === "rating"
            Text { text: "How hard was " + (root.ex ? root.ex.name : "") + "?  (1 easy – 10 max)"; color: root.fg; font.pixelSize: 20 }
            RpePicker { id: ratePicker }
            Flow {
              width: parent.width
              spacing: 12
              NotesField { id: rateNote; width: parent.width; minHeight: 80; placeholder: "note (optional): form, pain, what to change next time…"; onAccepted: if (rateNext.enabled) rateNext.clicked() }
              Btn {
                id: rateNext
                label: "Next exercise"
                hint: "Enter"
                primary: true
                enabled: ratePicker.value > 0
                onClicked: { root.train("rate", { rpe: ratePicker.value, note: rateNote.text }); rateNote.text = "" }
              }
            }
          }

          // Actions
          Flow {
            width: parent.width
            spacing: 12
            visible: root.phase === "ready" || root.phase === "running" || root.phase === "resting"
            Btn {
              label: root.phase === "running" ? "Stop" : "Start set " + root.setNo
              hint: "Space"
              primary: true
              big: true
              onClicked: root.train(root.phase === "running" ? "stop_set" : "start_set")
            }
            Btn {
              visible: root.phase === "resting" || (root.phase === "ready" && root.ex && root.ex.sets.length > 0)
              label: "Finish exercise"
              onClicked: root.train("end_sets")
            }
            Btn {
              visible: root.ex && root.ex.has_easier
              label: "Too hard"
              onClicked: root.train("swap_easier")
            }
            Btn {
              label: surface.skipOpen ? "Never mind" : "Skip…"
              onClicked: { surface.skipOpen = !surface.skipOpen; if (surface.skipOpen) skipReason.input.forceActiveFocus(); else surface.forceActiveFocus() }
            }
          }

          Flow {
            width: parent.width
            spacing: 12
            visible: surface.skipOpen && root.phase !== "summary"
            Field { id: skipReason; implicitWidth: Math.min(420, card.width - 200); placeholder: "why skip? (e.g. no bar, wrist pain)"; onAccepted: skipGo.clicked() }
            Btn {
              id: skipGo
              label: "Skip exercise"
              enabled: skipReason.text.trim().length >= 3
              onClicked: { root.train("skip", { reason: skipReason.text }); skipReason.text = "" }
            }
          }
        }
      }

        // Picture and instructions
        Column {
          id: side
          width: Math.min(420, Math.max(300, parent.width * 0.36))
          spacing: 10

          Rectangle {
            width: parent.width
            height: width * 0.72
            radius: 10
            visible: root.ex !== null && root.ex.image !== ""
            color: root.ex && root.ex.image_source === "stick figure" ? root.panel : "#f5f5f2"
            border.width: 1
            border.color: root.line
            clip: true
            Image {
              anchors.fill: parent
              anchors.margins: 8
              source: root.ex && root.ex.image ? "file://" + root.ex.image : ""
              fillMode: Image.PreserveAspectFit
              asynchronous: true
              smooth: true
              mipmap: true
              sourceSize.width: 840
            }
          }
          Text {
            width: parent.width
            visible: root.ex !== null && root.ex.image !== ""
            text: root.ex ? root.ex.image_source.replace(/^book: /, "").replace(/\.(epub|pdf)$/, "").replace(/_/g, ":") : ""
            elide: Text.ElideRight
            color: root.muted
            font.pixelSize: 12
          }

          Rectangle {
            width: parent.width
            height: Math.min(instructions.implicitHeight + 32, surface.height * (root.ex && root.ex.image ? 0.42 : 0.7))
            radius: 10
            color: root.panel
            border.width: 1
            border.color: root.line
            visible: root.ex !== null

            Flickable {
              anchors.fill: parent
              anchors.margins: 16
              contentHeight: instructions.implicitHeight
              clip: true
              boundsBehavior: Flickable.StopAtBounds

              Column {
                id: instructions
                width: parent.width
                spacing: 14
                InfoList { title: "Steps"; items: root.ex ? root.ex.steps : []; numbered: true; visible: surface.showCues && items.length > 0 }
                InfoList { title: "Cues"; items: root.ex ? root.ex.cues : [] }
                InfoList { title: "Common mistakes"; items: root.ex ? root.ex.mistakes : []; visible: surface.showCues && items.length > 0 }
                InfoList {
                  title: "Breathing"
                  items: root.ex && root.ex.breathing ? [root.ex.breathing] : []
                  visible: surface.showCues && items.length > 0
                }
                InfoList {
                  title: "Variations"
                  items: root.ex ? [].concat(root.ex.easier_name ? ["Easier: " + root.ex.easier_name] : [],
                                             root.ex.harder_name ? ["Harder: " + root.ex.harder_name] : []) : []
                  visible: surface.showCues && items.length > 0
                }
                Text {
                  width: parent.width
                  visible: surface.showCues && root.ex && root.ex.sources.length > 0
                  wrapMode: Text.WordWrap
                  text: root.ex ? "From: " + root.ex.sources.join(" · ") : ""
                  color: root.muted
                  font.pixelSize: 12
                }
              }
            }
          }
        }
      }

      // Session summary
      Rectangle {
        width: parent.width
        height: summary.implicitHeight + 48
        radius: 10
        color: root.panel
        border.width: 1
        border.color: root.line
        visible: root.phase === "summary"

        Column {
          id: summary
          x: 24; y: 24
          width: parent.width - 48
          spacing: 14
          Text { text: "Session done — how hard was it overall?"; color: root.fg; font.pixelSize: 24; font.weight: Font.DemiBold }
          RpePicker { id: sessionPicker }
          NotesField { id: sessionNotes; width: parent.width; placeholder: "notes: how it felt, pain, energy, sleep…"; onAccepted: if (finishSession.enabled) finishSession.clicked() }
          Text { text: "From your watch (optional)"; color: root.muted; font.pixelSize: 14 }
          Flow {
            width: parent.width
            spacing: 12
            Field { id: calories; placeholder: "calories"; suffix: "kcal"; input.validator: IntValidator { bottom: 0; top: 5000 } }
            Field { id: avgHr; placeholder: "avg HR"; suffix: "bpm"; input.validator: IntValidator { bottom: 30; top: 230 } }
            Field { id: bodyWeight; placeholder: "body weight"; suffix: "kg"; input.validator: DoubleValidator { bottom: 20; top: 300; decimals: 1 } }
          }
          Btn {
            id: finishSession
            label: "Finish session"
            primary: true
            big: true
            enabled: sessionPicker.value > 0
            onClicked: {
              var args = { rpe: sessionPicker.value, notes: sessionNotes.text }
              if (calories.text) args.calories = parseInt(calories.text)
              if (avgHr.text) args.avg_hr = parseInt(avgHr.text)
              if (bodyWeight.text) args.body_weight = parseFloat(bodyWeight.text.replace(",", "."))
              root.train("finish", args)
            }
          }
        }
      }

      // Override
      Flow {
        width: parent.width
        spacing: 12
        visible: root.st && root.st.lock && root.st.lock.overridable
        Btn {
          visible: root.st && !root.st.override
          label: root.overrideOpen ? "Never mind" : "Override…"
          onClicked: { root.overrideOpen = !root.overrideOpen; root.message = ""; if (root.overrideOpen) phrase.input.forceActiveFocus(); else surface.forceActiveFocus() }
        }
        Btn {
          visible: root.st && root.st.override
          label: "Cancel override"
          onClicked: root.send({ cmd: "cancel-override" })
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
        Field {
          id: phrase
          width: parent.width
          onAccepted: { root.send({ cmd: "override", phrase: text }); text = ""; root.overrideOpen = false }
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

  // -- the lock surface (and a preview window for development) -----------------------------

  readonly property bool preview: Quickshell.env("SPORTLOCK_PREVIEW") === "1"

  WlSessionLock {
    id: lock
    locked: false

    WlSessionLockSurface {
      color: root.bg
      TrainingScreen { anchors.fill: parent }
    }
  }

  FloatingWindow {
    visible: root.preview
    implicitWidth: 1280
    implicitHeight: 1000
    color: root.bg
    TrainingScreen { anchors.fill: parent }
  }
}
