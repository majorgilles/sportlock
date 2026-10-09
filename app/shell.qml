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

  // Requests to the service, one at a time: {payload, done(response), fail(message)}.
  property var queue: []
  property var current: null

  function send(payload, done, fail) {
    queue = queue.concat([{ payload: payload, done: done, fail: fail }])
    pump()
  }

  function pump() {
    // `current` marks a request in flight: Process.running doesn't flip to true synchronously.
    if (current !== null || queue.length === 0) return
    current = queue[0]
    queue = queue.slice(1)
    request.command = [cli, "raw", JSON.stringify(current.payload)]
    request.running = true
  }

  // -- schedule & settings ------------------------------------------------------------------

  property string tab: Quickshell.env("SPORTLOCK_TAB") || "calendar"

  // -- calendar -------------------------------------------------------------------------------

  property var calendar: null
  property string selectedDay: ""

  function loadCalendar() {
    send({ cmd: "calendar" }, function(response) {
      root.calendar = response.calendar
      if (!root.selectedDay) root.selectedDay = response.calendar.today
    })
  }

  function dayData(date) {
    if (!calendar) return null
    for (var i = 0; i < calendar.days.length; i++) if (calendar.days[i].date === date) return calendar.days[i]
    return null
  }

  function targetText(t) {
    if (!t) return ""
    var work = t.reps ? t.reps[0] + "–" + t.reps[1] + " reps" : (t.seconds >= 120 && t.sets === 1
               ? Math.round(t.seconds / 60) + " min" : t.seconds + " s")
    return (t.sets > 1 ? t.sets + " × " : "") + work + (t.sides ? " per side" : "")
  }

  function setsText(sets) {
    return sets.map(function(s) {
      var x = s.reps !== null && s.reps !== undefined ? s.reps + "" : Math.round(s.seconds) + " s"
      return s.load_kg ? x + " +" + s.load_kg + " kg" : x
    }).join(", ")
  }

  function modeColor(mode) { return mode === "hard" ? root.accent : mode === "rest" ? root.muted : root.fg }
  property var settings: null
  property int rev: 0  // bumped when settings are mutated in place (keeps text fields' focus)
  property string settingsMessage: ""
  property bool settingsOk: false
  property bool settingsFrozen: false
  readonly property var dayNames: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
  readonly property var dayLabels: ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

  function loadSettings() {
    send({ cmd: "settings-get" }, function(response) {
      root.settings = response.settings
      root.settingsFrozen = response.frozen
      if (response.pending) {
        root.settingsOk = true
        root.settingsMessage = "Saved changes are waiting for the current lock to end."
      }
    })
  }

  function toggleDay(i, day) {
    var days = settings.locks[i].days
    var at = days.indexOf(day)
    if (at >= 0) days.splice(at, 1); else days.push(day)
    days.sort(function(a, b) { return dayNames.indexOf(a) - dayNames.indexOf(b) })
    rev += 1
  }

  function addLock() {
    var next = JSON.parse(JSON.stringify(settings))
    next.locks.push({ days: ["mon", "tue", "wed", "thu", "fri"], at: "18:00", minutes: 30 })
    settings = next
  }

  function removeLock(i) {
    var next = JSON.parse(JSON.stringify(settings))
    next.locks.splice(i, 1)
    settings = next
  }

  function saveSettings() {
    send({ cmd: "settings-save", settings: settings }, function(response) {
      root.settingsOk = true
      root.settingsMessage = response.pending
        ? "Saved. A lock is active or due within 10 minutes, so this applies once it's over."
        : "Saved and applied."
      stateFile.reload()
    }, function(error) {
      root.settingsOk = false
      root.settingsMessage = error
    })
  }

  function nextLockText() {
    try {
      var st = JSON.parse(stateFile.text())
      if (!st.next_lock) return "No upcoming lock."
      var start = new Date(st.next_lock.start)
      var minutes = Math.round((st.next_lock.end - st.next_lock.start) / 60000)
      return "Next lock: " + start.toLocaleString(Qt.locale(), "dddd HH:mm") + ", " + minutes + " min"
    } catch (e) { return "" }
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

  // -- the coach's memory ---------------------------------------------------------------------

  property var memory: []
  readonly property var memoryTopics: ({ body: "Body", preferences: "Preferences", progress: "Progress",
                                        plans: "Plans", context: "Setup", coaching: "Coaching" })

  function loadMemory() { send({ cmd: "memory-get" }, function(response) { root.memory = response.notes }) }
  function forgetNote(id) { send({ cmd: "memory-forget", id: id }, function() { root.loadMemory() }) }

  Component.onCompleted: {
    loadMemory()
    loadCalendar()
    loadSettings()
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
    id: stateFile
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
      var item = root.current
      var error = String(err.text || out.text).trim().replace(/^sportlock: /, "")
      if (code !== 0) {
        if (item && item.fail) item.fail(error)
        else { root.saved = false; root.message = error }
      } else {
        try { if (item && item.done) item.done(JSON.parse(out.text)) } catch (e) { root.message = String(e) }
      }
      root.current = null
      root.pump()
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
    signal edited(string value)
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
      onTextEdited: if (activeFocus) field.edited(text)  // only real typing, never programmatic changes
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
    id: window
    visible: true
    title: "sportlock"
    // Closing the window (button, Esc, or the window manager) ends the app; nothing lingers.
    onVisibleChanged: if (!visible) Qt.quit()

    Shortcut { sequences: ["Escape"]; onActivated: Qt.quit() }

    Rectangle {
      anchors.top: parent.top
      anchors.right: parent.right
      anchors.topMargin: 20
      anchors.rightMargin: 40
      z: 10
      width: closeText.implicitWidth + 32
      height: 38
      radius: 19
      color: closeArea.containsMouse ? Qt.rgba(1, 1, 1, 0.08) : "transparent"
      border.width: 1
      border.color: root.muted
      Text { id: closeText; anchors.centerIn: parent; text: "✕  Close"; color: root.fg; font.pixelSize: 15 }
      MouseArea { id: closeArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: Qt.quit() }
    }
    implicitWidth: 1000
    implicitHeight: 940
    color: root.bg

    Row {
      id: tabs
      x: 40; y: 20
      spacing: 8
      Chip { label: "Calendar"; on: root.tab === "calendar"; onClicked: { root.tab = "calendar"; root.loadCalendar() } }
      Chip { label: "Profile"; on: root.tab === "profile"; onClicked: { root.tab = "profile"; root.loadMemory() } }
      Chip { label: "Schedule & settings"; on: root.tab === "settings"; onClicked: { root.tab = "settings"; root.loadSettings() } }
    }

    Flickable {
      anchors.top: tabs.bottom
      anchors.topMargin: 4
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      visible: root.tab === "calendar"
      contentHeight: cal.implicitHeight + 64
      clip: true
      boundsBehavior: Flickable.StopAtBounds

      Column {
        id: cal
        x: 40; y: 32
        width: parent.width - 80
        spacing: 16

        Column {
          spacing: 6
          Text { text: "Calendar"; color: root.fg; font.pixelSize: 30; font.weight: Font.DemiBold }
          Text {
            width: cal.width
            wrapMode: Text.WordWrap
            color: root.muted
            font.pixelSize: 15
            text: root.calendar ? "Last 7 days: " + root.calendar.load.sessions_last_7_days + (root.calendar.load.sessions_last_7_days === 1 ? " session, " : " sessions, ")
                  + root.calendar.load.minutes_last_7_days + " min. Click a day for details." : "Loading…"
          }
        }

        Row {
          spacing: 6
          Repeater {
            model: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            delegate: Text {
              required property var modelData
              width: (cal.width - 36) / 7
              text: modelData
              color: root.muted
              font.pixelSize: 13
              horizontalAlignment: Text.AlignHCenter
            }
          }
        }

        Grid {
          columns: 7
          spacing: 6
          Repeater {
            model: root.calendar ? root.calendar.days : []
            delegate: Rectangle {
              id: cell
              required property var modelData
              readonly property date when: new Date(modelData.date + "T12:00:00")
              width: (cal.width - 36) / 7
              height: 104
              radius: 8
              color: root.selectedDay === modelData.date ? Qt.rgba(1, 1, 1, 0.08) : root.panel
              border.width: modelData.today ? 2 : 1
              border.color: modelData.today ? root.accent : root.line
              opacity: modelData.past && modelData.sessions.length === 0 && modelData.locks.length === 0 ? 0.55 : 1
              clip: true

              Column {
                x: 8; y: 6
                width: parent.width - 16
                spacing: 3
                Text {
                  text: cell.when.getDate() === 1 || cell.modelData.today ? Qt.formatDate(cell.when, "d MMM") : cell.when.getDate()
                  color: cell.modelData.today ? root.accent : root.muted
                  font.pixelSize: 13
                  font.weight: cell.modelData.today ? Font.DemiBold : Font.Normal
                }
                Repeater {
                  model: cell.modelData.sessions
                  delegate: Text {
                    required property var modelData
                    width: cell.width - 16
                    elide: Text.ElideRight
                    text: (modelData.status === "finished" ? "✓ " : "✗ ") + modelData.title
                          + (modelData.minutes !== null ? " · " + modelData.minutes + "′" : "")
                    color: modelData.status === "finished" ? root.accent : root.urgent
                    font.pixelSize: 12
                  }
                }
                Repeater {
                  model: cell.modelData.locks.filter(function(l) { return l.outcome === "rest" || l.outcome === "override" })
                  delegate: Text {
                    required property var modelData
                    text: modelData.outcome === "rest" ? "☾ rest day" : "⏻ overridden"
                    color: root.muted
                    font.pixelSize: 12
                  }
                }
                Repeater {
                  model: cell.modelData.planned || []
                  delegate: Text {
                    required property var modelData
                    width: cell.width - 16
                    elide: Text.ElideRight
                    text: modelData.mode === "rest" ? modelData.time + " ☾ rest"
                          : modelData.mode === "later" ? modelData.time + " · " + modelData.minutes + "′"
                          : modelData.time + " " + modelData.mode + " · " + modelData.minutes + "′"
                    color: modelData.mode === "later" ? root.muted : root.modeColor(modelData.mode)
                    font.pixelSize: 12
                    font.weight: modelData.next ? Font.DemiBold : Font.Normal
                  }
                }
              }
              MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.selectedDay = cell.modelData.date }
            }
          }
        }

        // Details of the selected day
        Rectangle {
          readonly property var day: root.dayData(root.selectedDay)
          id: detail
          width: cal.width
          height: detailColumn.implicitHeight + 40
          radius: 10
          color: root.panel
          border.width: 1
          border.color: root.line
          visible: day !== null

          Column {
            id: detailColumn
            x: 20; y: 20
            width: parent.width - 40
            spacing: 14

            Text {
              text: detail.day ? Qt.formatDate(new Date(detail.day.date + "T12:00:00"), "dddd d MMMM") : ""
              color: root.fg
              font.pixelSize: 20
              font.weight: Font.DemiBold
            }
            Text {
              visible: detail.day !== null && detail.day.sessions.length === 0 && !(detail.day.planned && detail.day.planned.length)
                       && detail.day.locks.length === 0
              text: "Nothing on this day."
              color: root.muted
              font.pixelSize: 15
            }

            Repeater {
              model: detail.day ? detail.day.sessions : []
              delegate: Column {
                required property var modelData
                width: detailColumn.width
                spacing: 6
                Text {
                  width: parent.width
                  wrapMode: Text.WordWrap
                  text: modelData.title + "  ·  " + modelData.time + (modelData.minutes !== null ? ", " + modelData.minutes + " min" : "")
                        + "  ·  " + (modelData.status === "finished" ? "done" : modelData.status)
                        + (modelData.rpe ? "  ·  effort " + modelData.rpe : "") + (modelData.day_type ? "  ·  " + modelData.day_type + " day" : "")
                  color: modelData.status === "finished" ? root.accent : root.urgent
                  font.pixelSize: 16
                }
                Text {
                  visible: modelData.notes.length > 0
                  width: parent.width
                  wrapMode: Text.WordWrap
                  text: modelData.notes
                  color: root.muted
                  font.pixelSize: 14
                }
                Repeater {
                  model: modelData.exercises
                  delegate: Text {
                    required property var modelData
                    width: detailColumn.width
                    wrapMode: Text.WordWrap
                    text: "•  " + modelData.name + ":  "
                          + (modelData.status === "skipped" ? "skipped (" + modelData.skip_reason + ")"
                             : modelData.sets.length ? root.setsText(modelData.sets) + (modelData.rpe ? "  · effort " + modelData.rpe : "")
                             : "not done")
                          + (modelData.note ? "  — " + modelData.note : "")
                    color: modelData.status === "done" ? root.fg : root.muted
                    font.pixelSize: 14
                  }
                }
                Column {
                  visible: !!modelData.recommendations && modelData.recommendations.length > 0
                  width: detailColumn.width
                  spacing: 4
                  topPadding: 4
                  Text { text: "Coach's feedback"; color: root.accent; font.pixelSize: 12; font.capitalization: Font.AllUppercase; font.letterSpacing: 1 }
                  Repeater {
                    model: modelData.recommendations || []
                    delegate: Text {
                      required property var modelData
                      width: detailColumn.width
                      wrapMode: Text.WordWrap
                      text: "•  " + (modelData.about ? modelData.about + ": " : "") + modelData.advice
                      color: root.fg
                      font.pixelSize: 14
                    }
                  }
                }
              }
            }

            Repeater {
              model: detail.day ? detail.day.locks.filter(function(l) { return l.outcome === "rest" }) : []
              delegate: Text {
                required property var modelData
                width: detailColumn.width
                wrapMode: Text.WordWrap
                text: "☾ Rest day instead of the " + modelData.time + " lock" + (modelData.reason ? ": " + modelData.reason : "")
                color: root.muted
                font.pixelSize: 15
              }
            }

            Repeater {
              model: detail.day && detail.day.planned ? detail.day.planned : []
              delegate: Column {
                required property var modelData
                width: detailColumn.width
                spacing: 6
                Text {
                  width: parent.width
                  wrapMode: Text.WordWrap
                  text: modelData.mode === "rest" ? "Planned " + modelData.time + ": rest day"
                        : modelData.mode === "later" ? "Lock at " + modelData.time + ", up to " + modelData.minutes + " min"
                        : "Planned " + modelData.time + ": " + modelData.mode + " session, " + modelData.minutes + " min"
                          + (modelData.minutes < modelData.scheduled_minutes ? " (shortened from " + modelData.scheduled_minutes + ")" : "")
                  color: modelData.mode === "later" ? root.fg : root.modeColor(modelData.mode)
                  font.pixelSize: 16
                }
                Text {
                  visible: !!modelData.reason
                  width: parent.width
                  wrapMode: Text.WordWrap
                  text: (modelData.mode === "later" ? "" : (modelData.decided ? "Decided: " : "Expected: ")) + (modelData.reason || "")
                  color: root.muted
                  font.pixelSize: 14
                }
                Text {
                  visible: !!modelData.title
                  width: parent.width
                  wrapMode: Text.WordWrap
                  text: (modelData.title || "") + (modelData.rationale ? " — " + modelData.rationale : "")
                  color: root.fg
                  font.pixelSize: 14
                }
                Repeater {
                  model: modelData.exercises || []
                  delegate: Text {
                    required property var modelData
                    width: detailColumn.width
                    wrapMode: Text.WordWrap
                    text: "•  " + modelData.name + ":  " + root.targetText(modelData.target)
                          + (modelData.target.progress ? "  — " + modelData.target.progress : "")
                    color: root.fg
                    font.pixelSize: 14
                  }
                }
                Column {
                  visible: !!modelData.recommendations && modelData.recommendations.length > 0
                  width: detailColumn.width
                  spacing: 4
                  topPadding: 4
                  Text { text: "Coach, on your last session"; color: root.accent; font.pixelSize: 12; font.capitalization: Font.AllUppercase; font.letterSpacing: 1 }
                  Repeater {
                    model: modelData.recommendations || []
                    delegate: Text {
                      required property var modelData
                      width: detailColumn.width
                      wrapMode: Text.WordWrap
                      text: "•  " + (modelData.about ? modelData.about + ": " : "") + modelData.advice
                      color: root.fg
                      font.pixelSize: 14
                    }
                  }
                }
                Text {
                  visible: !!modelData.next && !modelData.exercises
                  text: "The exact exercises are chosen when the lock starts."
                  color: root.muted
                  font.pixelSize: 13
                }
              }
            }
          }
        }
      }
    }

    Flickable {
      anchors.top: tabs.bottom
      anchors.topMargin: 4
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      visible: root.tab === "profile"
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

        Section {
          visible: !root.firstTime
          title: "What your coach remembers"
          hint: root.memory.length ? "Notes the coach keeps about you and rewrites after every session, on top of your recent sessions. Remove anything that is wrong; it won't come back unless something new shows it."
                                   : "Nothing yet. After each session the coach writes down what it learns about you here."
          Repeater {
            model: root.memory
            delegate: Row {
              required property var modelData
              width: form.width
              spacing: 12
              Text {
                width: 96
                text: root.memoryTopics[modelData.topic] || modelData.topic
                color: root.muted
                font.pixelSize: 13
                topPadding: 2
              }
              Text {
                width: parent.width - 96 - 48 - 24
                wrapMode: Text.WordWrap
                text: modelData.note
                color: root.fg
                font.pixelSize: 14
              }
              Text {
                width: 48
                horizontalAlignment: Text.AlignRight
                text: "✕"
                color: forgetArea.containsMouse ? root.urgent : root.muted
                font.pixelSize: 15
                MouseArea { id: forgetArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onClicked: root.forgetNote(modelData.id) }
              }
            }
          }
        }
      }
    }
      Flickable {
      anchors.top: tabs.bottom
      anchors.topMargin: 4
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      visible: root.tab === "settings"
      contentHeight: settingsForm.implicitHeight + 64
      clip: true
      boundsBehavior: Flickable.StopAtBounds

      Column {
        id: settingsForm
        x: 40; y: 32
        width: parent.width - 80
        spacing: 26
        visible: root.settings !== null

        Column {
          spacing: 6
          Text { text: "Schedule & settings"; color: root.fg; font.pixelSize: 30; font.weight: Font.DemiBold }
          Text {
            width: settingsForm.width
            wrapMode: Text.WordWrap
            text: root.rev >= 0 ? root.nextLockText() : ""
            color: root.muted
            font.pixelSize: 15
          }
        }

        Section {
          title: "Locks"
          hint: "When off, nothing locks on schedule (you can still start a session yourself)."
          Row {
            spacing: 8
            // `root.rev` in the binding: settings are edited in place, so the chips re-read on each change.
            Chip { label: "On"; on: root.rev >= 0 && !!root.settings && root.settings.enabled === true
                   onClicked: { root.settings.enabled = true; root.rev += 1 } }
            Chip { label: "Off"; on: root.rev >= 0 && !!root.settings && root.settings.enabled !== true
                   onClicked: { root.settings.enabled = false; root.rev += 1 } }
          }
        }

        Section {
          title: "Lock times"
          hint: "Each lock lasts up to its minutes, or until you finish the session. Overlapping locks merge."
          Repeater {
            model: root.settings ? root.settings.locks : []
            delegate: Rectangle {
              id: lockCard
              required property var modelData
              required property int index
              width: settingsForm.width
              height: lockRow.implicitHeight + 24
              radius: 8
              color: root.panel
              border.width: 1
              border.color: root.line
              Flow {
                id: lockRow
                x: 12; y: 12
                width: parent.width - 24
                spacing: 8
                Repeater {
                  model: 7
                  delegate: Chip {
                    required property int index
                    readonly property string day: root.dayNames[index]
                    implicitWidth: 46
                    label: root.dayLabels[index]
                    on: root.rev >= 0 && root.settings.locks[lockCard.index].days.indexOf(day) >= 0
                    onClicked: root.toggleDay(lockCard.index, day)
                  }
                }
                Field {
                  implicitWidth: 90
                  placeholder: "18:00"
                  text: lockCard.modelData.at
                  input.validator: RegularExpressionValidator { regularExpression: /^([01]?[0-9]|2[0-3])(:[0-5]?[0-9]?)?$/ }
                  onEdited: function(value) { root.settings.locks[lockCard.index].at = value }
                }
                Field {
                  implicitWidth: 120
                  placeholder: "minutes"
                  text: String(lockCard.modelData.minutes)
                  input.validator: IntValidator { bottom: 1; top: 240 }
                  onEdited: function(value) { root.settings.locks[lockCard.index].minutes = value }
                }
                Text { height: 42; verticalAlignment: Text.AlignVCenter; text: "min"; color: root.muted; font.pixelSize: 14 }
                Chip { label: "Remove"; onClicked: root.removeLock(lockCard.index) }
              }
            }
          }
          Chip { label: "+ Add lock time"; onClicked: root.addLock() }
        }

        Section {
          title: "Limits and reminders"
          Flow {
            width: parent.width; spacing: 12
            Column {
              spacing: 4
              Text { text: "Max lock minutes per day"; color: root.muted; font.pixelSize: 13 }
              Field {
                implicitWidth: 120
                text: root.settings ? String(root.settings.max_minutes_per_day) : ""
                input.validator: IntValidator { bottom: 1; top: 1440 }
                onEdited: function(value) { root.settings.max_minutes_per_day = value }
              }
            }
            Column {
              spacing: 4
              Text { text: "Warn before a lock (minutes)"; color: root.muted; font.pixelSize: 13 }
              Field {
                implicitWidth: 160
                placeholder: "10, 2"
                text: root.settings ? root.settings.warn_minutes.join(", ") : ""
                onEdited: function(value) { root.settings.warn_minutes = value.split(/[ ,]+/).filter(function(v) { return v.length > 0 }) }
              }
            }
            Column {
              spacing: 4
              Text { text: "Popup with the first warning"; color: root.muted; font.pixelSize: 13 }
              Row {
                spacing: 8
                Chip { label: "On"; on: root.rev >= 0 && !!root.settings && root.settings.warn_popup === true
                       onClicked: { root.settings.warn_popup = true; root.rev += 1 } }
                Chip { label: "Off"; on: root.rev >= 0 && !!root.settings && root.settings.warn_popup !== true
                       onClicked: { root.settings.warn_popup = false; root.rev += 1 } }
              }
            }
            Column {
              spacing: 4
              Text { text: "Get-ready countdown (seconds)"; color: root.muted; font.pixelSize: 13 }
              Field {
                implicitWidth: 120
                text: root.settings ? String(root.settings.lead_in_seconds) : ""
                input.validator: IntValidator { bottom: 0; top: 30 }
                onEdited: function(value) { root.settings.lead_in_seconds = value }
              }
            }
          }
        }

        Section {
          title: "Recovery and rest days"
          hint: "After a big session the coach can shorten the next lock to a recovery session, or make it a rest day. These limits always apply."
          Row {
            spacing: 8
            Chip { label: "Rest days allowed"; on: root.rev >= 0 && !!root.settings && root.settings.allow_rest_days === true
                   onClicked: { root.settings.allow_rest_days = true; root.rev += 1 } }
            Chip { label: "No rest days"; on: root.rev >= 0 && !!root.settings && root.settings.allow_rest_days !== true
                   onClicked: { root.settings.allow_rest_days = false; root.rev += 1 } }
          }
          Flow {
            width: parent.width; spacing: 12
            Column {
              spacing: 4
              Text { text: "Max rest days in a row"; color: root.muted; font.pixelSize: 13 }
              Field {
                implicitWidth: 120
                text: root.settings ? String(root.settings.max_rest_days_in_a_row) : ""
                input.validator: IntValidator { bottom: 0; top: 6 }
                onEdited: function(value) { root.settings.max_rest_days_in_a_row = value }
              }
            }
            Column {
              spacing: 4
              Text { text: "Min sessions per 7 days for a rest day"; color: root.muted; font.pixelSize: 13 }
              Field {
                implicitWidth: 120
                text: root.settings ? String(root.settings.min_sessions_per_week) : ""
                input.validator: IntValidator { bottom: 0; top: 7 }
                onEdited: function(value) { root.settings.min_sessions_per_week = value }
              }
            }
            Column {
              spacing: 4
              Text { text: "Recovery lock length (minutes)"; color: root.muted; font.pixelSize: 13 }
              Field {
                implicitWidth: 120
                text: root.settings ? String(root.settings.recovery_minutes) : ""
                input.validator: IntValidator { bottom: 5; top: 60 }
                onEdited: function(value) { root.settings.recovery_minutes = value }
              }
            }
          }
        }

        Section {
          title: "Override"
          hint: "What you type on the lock screen to skip a session, and how long you then have to wait."
          Field {
            width: parent.width
            text: root.settings ? root.settings.override_phrase : ""
            onEdited: function(value) { root.settings.override_phrase = value }
          }
          Row {
            spacing: 10
            Field {
              implicitWidth: 120
              text: root.settings ? String(root.settings.override_wait_seconds) : ""
              input.validator: IntValidator { bottom: 1; top: 3600 }
              onEdited: function(value) { root.settings.override_wait_seconds = value }
            }
            Text { anchors.verticalCenter: parent.verticalCenter; text: "seconds to wait"; color: root.muted; font.pixelSize: 14 }
          }
        }

        Row {
          spacing: 16
          Rectangle {
            width: saveSettingsText.implicitWidth + 48; height: 50; radius: 6
            color: root.accent
            Text { id: saveSettingsText; anchors.centerIn: parent; text: "Save"; color: root.bg; font.pixelSize: 18; font.weight: Font.DemiBold }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.saveSettings() }
          }
          Text {
            anchors.verticalCenter: parent.verticalCenter
            width: settingsForm.width - saveSettingsText.implicitWidth - 70
            wrapMode: Text.WordWrap
            text: root.settingsMessage
            color: root.settingsOk ? root.accent : root.urgent
            font.pixelSize: 14
          }
        }
      }
    }
  }
}
