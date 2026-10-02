// sportlock app window. For now: the profile (onboarding) form. Talks to the service through
// `sportlock raw`, reads theme colours from the service's state file.
import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
  id: root

  readonly property string cli: Quickshell.env("SPORTLOCK_BIN") || "sportlock"
  readonly property string statePath: Quickshell.env("SPORTLOCK_STATE")

  property var theme: ({})
  readonly property color bg: theme.background || "#121212"
  readonly property color fg: theme.foreground || "#bebebe"
  readonly property color accent: theme.accent || "#e68e0d"
  readonly property color muted: theme.muted || "#555555"
  readonly property color urgent: theme.urgent || "#d35f5f"
  readonly property color panel: Qt.rgba(1, 1, 1, 0.04)
  readonly property color line: Qt.rgba(1, 1, 1, 0.09)

  property var choices: ({ experience: [], goals: [], equipment: [], locations: [] })
  property string experience: ""
  property var goals: []
  property var equipment: []
  property string location: ""
  property bool firstTime: true
  property string message: ""
  property bool saved: false

  readonly property var equipmentLabels: ({
    "chair": "Chair", "table": "Sturdy table", "bench": "Bench / sofa edge", "doorway": "Doorway",
    "bar": "Pull-up bar", "dip-bars": "Dip bars", "parallettes": "Parallettes", "rings": "Rings",
    "bands": "Resistance bands", "anchor": "Something to hook your feet under"
  })

  function has(list, value) { return list.indexOf(value) >= 0 }
  function toggled(list, value) {
    var next = list.slice()
    var i = next.indexOf(value)
    if (i >= 0) next.splice(i, 1); else next.push(value)
    return next
  }

  function send(payload, done) {
    request.done = done
    request.command = [cli, "raw", JSON.stringify(payload)]
    request.running = true
  }

  function save() {
    send({ cmd: "profile-save", profile: {
      experience: experience, years_training: years.text, goals: goals, equipment: equipment,
      injuries: injuries.text, age: age.text, sex: sex.text, weight_kg: weight.text.replace(",", "."),
      location: location
    } }, function(response) {
      root.saved = true
      root.message = root.firstTime ? "Saved. Your coach is planning your first session — scheduled locks are now active."
                                    : "Saved. Your coach will re-plan the next session."
      root.firstTime = false
    })
  }

  Component.onCompleted: {
    send({ cmd: "profile-get" }, function(response) {
      root.choices = response.choices
      var p = response.profile
      if (p) {
        root.firstTime = false
        root.experience = p.experience
        root.goals = p.goals
        root.equipment = p.equipment
        root.location = p.location
        years.text = p.years_training !== null && p.years_training !== undefined ? String(p.years_training) : ""
        injuries.text = p.injuries || ""
        age.text = p.age ? String(p.age) : ""
        sex.text = p.sex || ""
        weight.text = p.weight_kg ? String(p.weight_kg) : ""
      } else {
        root.equipment = ["chair", "table", "bench", "doorway"]
        root.location = response.choices.locations[0]
      }
    })
  }

  FileView {
    path: root.statePath
    blockLoading: true
    printErrors: false
    onLoaded: { try { root.theme = JSON.parse(text()).theme || {} } catch (e) {} }
  }

  Process {
    id: request
    property var done: null
    stdout: StdioCollector { id: out }
    stderr: StdioCollector { id: err }
    onExited: function(code) {
      if (code !== 0) {
        root.saved = false
        root.message = String(err.text || out.text).trim().replace(/^sportlock: /, "")
        return
      }
      try { if (request.done) request.done(JSON.parse(out.text)) } catch (e) { root.message = String(e) }
    }
  }

  // -- components ----------------------------------------------------------------------------

  component Chip: Rectangle {
    id: chip
    property string label: ""
    property bool on: false
    signal clicked()
    implicitWidth: chipText.implicitWidth + 32
    implicitHeight: 38
    radius: 19
    color: on ? root.accent : (area.containsMouse ? Qt.rgba(1, 1, 1, 0.07) : "transparent")
    border.width: 1
    border.color: on ? root.accent : root.muted
    Text { id: chipText; anchors.centerIn: parent; text: chip.label; color: chip.on ? root.bg : root.fg; font.pixelSize: 15 }
    MouseArea { id: area; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: chip.clicked() }
  }

  component Field: Rectangle {
    id: field
    property alias text: input.text
    property alias input: input
    property string placeholder: ""
    implicitWidth: 160
    implicitHeight: 42
    radius: 6
    color: "transparent"
    border.width: 1
    border.color: input.activeFocus ? root.accent : root.muted
    TextInput {
      id: input
      anchors.fill: parent
      anchors.margins: 11
      verticalAlignment: TextInput.AlignVCenter
      color: root.fg
      font.pixelSize: 16
      clip: true
    }
    Text {
      anchors.left: parent.left; anchors.leftMargin: 11; anchors.verticalCenter: parent.verticalCenter
      visible: input.text.length === 0; text: field.placeholder; color: root.muted; font.pixelSize: 15
    }
  }

  component Section: Column {
    property string title: ""
    property string hint: ""
    width: parent ? parent.width : 600
    spacing: 10
    Text { text: parent.title; color: root.accent; font.pixelSize: 13; font.capitalization: Font.AllUppercase; font.letterSpacing: 1 }
    Text { visible: parent.hint.length > 0; text: parent.hint; color: root.muted; font.pixelSize: 13; width: parent.width; wrapMode: Text.WordWrap }
  }

  // -- window ---------------------------------------------------------------------------------

  FloatingWindow {
    visible: true
    title: "sportlock"
    implicitWidth: 760
    implicitHeight: 900
    color: root.bg

    Flickable {
      anchors.fill: parent
      contentHeight: form.implicitHeight + 64
      clip: true
      boundsBehavior: Flickable.StopAtBounds

      Column {
        id: form
        x: 40; y: 32
        width: parent.width - 80
        spacing: 26

        Column {
          spacing: 6
          Text { text: root.firstTime ? "Welcome to sportlock" : "Your profile"; color: root.fg; font.pixelSize: 30; font.weight: Font.DemiBold }
          Text {
            width: form.width
            wrapMode: Text.WordWrap
            text: "Your coach uses this to pick exercises and difficulty. You can change it any time; your progress is kept."
            color: root.muted
            font.pixelSize: 15
          }
        }

        Section {
          title: "Training experience"
          Flow {
            width: parent.width; spacing: 8
            Repeater {
              model: root.choices.experience
              delegate: Chip { required property var modelData; label: modelData; on: root.experience === modelData; onClicked: root.experience = modelData }
            }
          }
          Row {
            spacing: 10
            Field { id: years; implicitWidth: 120; placeholder: "years"; input.validator: DoubleValidator { bottom: 0; top: 60 } }
            Text { anchors.verticalCenter: parent.verticalCenter; text: "years of regular training (optional)"; color: root.muted; font.pixelSize: 14 }
          }
        }

        Section {
          title: "Goals"
          hint: "Pick one or more."
          Flow {
            width: parent.width; spacing: 8
            Repeater {
              model: root.choices.goals
              delegate: Chip { required property var modelData; label: modelData; on: root.has(root.goals, modelData); onClicked: root.goals = root.toggled(root.goals, modelData) }
            }
          }
        }

        Section {
          title: "Equipment"
          hint: "What you can train with at home. A pull-up bar opens the whole pulling chain."
          Flow {
            width: parent.width; spacing: 8
            Repeater {
              model: root.choices.equipment
              delegate: Chip {
                required property var modelData
                label: root.equipmentLabels[modelData] || modelData
                on: root.has(root.equipment, modelData)
                onClicked: root.equipment = root.toggled(root.equipment, modelData)
              }
            }
          }
        }

        Section {
          title: "Injuries or limitations"
          hint: "Anything the coach should work around: old injuries, pain, mobility limits."
          Rectangle {
            width: parent.width
            height: Math.max(90, injuries.contentHeight + 24)
            radius: 6
            color: "transparent"
            border.width: 1
            border.color: injuries.activeFocus ? root.accent : root.muted
            TextEdit {
              id: injuries
              anchors.fill: parent
              anchors.margins: 12
              wrapMode: TextEdit.Wrap
              color: root.fg
              font.pixelSize: 15
              selectionColor: root.accent
            }
          }
        }

        Section {
          title: "About you (optional)"
          Flow {
            width: parent.width; spacing: 10
            Field { id: age; implicitWidth: 110; placeholder: "age"; input.validator: IntValidator { bottom: 10; top: 100 } }
            Field { id: sex; implicitWidth: 140; placeholder: "sex" }
            Field { id: weight; implicitWidth: 150; placeholder: "weight (kg)" }
          }
        }

        Section {
          title: "Where you train"
          Flow {
            width: parent.width; spacing: 8
            Repeater {
              model: root.choices.locations
              delegate: Chip { required property var modelData; label: modelData; on: root.location === modelData; onClicked: root.location = modelData }
            }
          }
        }

        Row {
          spacing: 16
          Rectangle {
            width: saveText.implicitWidth + 48; height: 50; radius: 6
            color: root.accent
            Text { id: saveText; anchors.centerIn: parent; text: root.firstTime ? "Start training" : "Save"; color: root.bg; font.pixelSize: 18; font.weight: Font.DemiBold }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.save() }
          }
          Text {
            anchors.verticalCenter: parent.verticalCenter
            width: form.width - saveText.implicitWidth - 70
            wrapMode: Text.WordWrap
            text: root.message
            color: root.saved ? root.accent : root.urgent
            font.pixelSize: 14
          }
        }
      }
    }
  }
}
