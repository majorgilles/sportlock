// sportlock app window: calendar, report, profile, and schedule & settings. Talks to the service through
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

  // -- the Spotify account (lists every playlist on the lock screen) ------------------------

  property var musicAccount: null
  property bool musicConnecting: false
  property string musicError: ""

  function loadMusicAccount() {
    send({ cmd: "music-account" }, function(response) {
      var wasConnecting = root.musicConnecting
      root.musicAccount = response.account
      root.musicConnecting = response.connecting
      root.musicError = response.error
      if (clientIdField.text === "") clientIdField.text = response.account.client_id
      if (wasConnecting && !response.connecting && response.account.connected) root.musicError = ""
    })
  }

  function connectSpotify() {
    root.musicError = ""
    send({ cmd: "music-login", client_id: clientIdField.text }, function() {
      root.musicConnecting = true
    }, function(error) { root.musicError = error })
  }

  function disconnectSpotify() {
    send({ cmd: "music-logout" }, function() { root.loadMusicAccount() })
  }

  Timer {
    interval: 1500
    repeat: true
    running: root.musicConnecting
    onTriggered: root.loadMusicAccount()
  }

  function loadSettings() {
    loadMusicAccount()
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

  function addPlaylist() {
    var next = JSON.parse(JSON.stringify(settings))
    next.music_playlists.push({ name: "", link: "" })
    settings = next
  }

  function removePlaylist(i) {
    var next = JSON.parse(JSON.stringify(settings))
    next.music_playlists.splice(i, 1)
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
  // -- the report ------------------------------------------------------------------------------

  property var report: null
  property bool reportWriting: false
  property string reportError: ""

  function loadReport() {
    send({ cmd: "trainee-report" }, function(response) {
      root.report = response.report
      root.reportWriting = response.writing
      root.reportError = response.error
    })
  }

  function writeReport() {
    root.reportError = ""
    send({ cmd: "trainee-report-write" }, function() { root.reportWriting = true },
         function(error) { root.reportError = error })
  }

  Timer {
    interval: 4000
    repeat: true
    running: root.reportWriting && root.tab === "report"
    onTriggered: root.loadReport()
  }

  function esc(text) { return String(text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;") }
  function dayText(iso) { return iso ? Qt.formatDate(new Date(iso.slice(0, 10) + "T12:00:00"), "ddd d MMM") : "" }
  function hoursText(minutes) {
    return minutes < 60 ? minutes + " min" : Math.floor(minutes / 60) + " h " + String(minutes % 60).padStart(2, "0")
  }
  function chainText(chain) {
    return ({ "push-horizontal": "Push", "push-vertical": "Overhead push", "dip": "Dip", "pull-horizontal": "Row / upper back",
              "pull-vertical": "Pull-up", "squat": "Squat / legs", "hinge": "Hips / hamstrings", "core": "Core (plank)",
              "core-flexion": "Abs", "calf": "Calves", "hip": "Side hip" })[chain] || chain
  }
  function exerciseLine(e) {
    if (e.status === "skipped") return root.esc(e.name) + " — <i>skipped" + (e.skip_reason ? ": " + root.esc(e.skip_reason) : "") + "</i>"
    var line = root.esc(e.name) + (e.sets.length ? " — " + root.setsText(e.sets) : "")
    if (e.rpe) line += " · effort " + e.rpe
    if (e.note) line += " · <i>“" + root.esc(e.note) + "”</i>"
    return line
  }

  function forgetNote(id) { send({ cmd: "memory-forget", id: id }, function() { root.loadMemory() }) }

  Component.onCompleted: {
    loadMemory()
    loadCalendar()
    loadReport()
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

  // One session in the journal: what was done, what you said, what changed, what the coach answered.
  component JournalEntry: Rectangle {
    id: entryBox
    property var entry: null
    property bool open: false
    width: parent ? parent.width : 600
    height: entryCol.implicitHeight + 28
    radius: 8
    color: root.panel
    border.width: 1
    border.color: root.line
    Column {
      id: entryCol
      x: 18; y: 14
      width: parent.width - 36
      spacing: 8
      Item {
        width: parent.width
        height: entryHead.implicitHeight
        Text {
          id: entryHead
          width: parent.width - 90
          wrapMode: Text.WordWrap
          textFormat: Text.StyledText
          text: entryBox.entry ? "<b>" + root.dayText(entryBox.entry.date) + "</b>  ·  " + root.esc(entryBox.entry.title || "Session")
                + "  ·  " + entryBox.entry.minutes + " min" + (entryBox.entry.effort ? "  ·  effort " + entryBox.entry.effort + "/10" : "")
                + (entryBox.entry.status !== "finished" ? "  ·  <font color='" + root.urgent + "'>" + entryBox.entry.status + "</font>" : "")
                + (entryBox.entry.kind === "manual" ? "  ·  started by you" : "") : ""
          color: root.fg
          font.pixelSize: 16
        }
        Text {
          anchors.right: parent.right
          text: entryBox.open ? "less ▴" : "details ▾"
          color: root.muted
          font.pixelSize: 13
          MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: entryBox.open = !entryBox.open }
        }
      }
      Text {
        visible: entryBox.entry && !!entryBox.entry.notes
        width: parent.width
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
        text: entryBox.entry && entryBox.entry.notes ? "“" + entryBox.entry.notes.trim() + "”" : ""
        color: root.fg
        font.pixelSize: 15
        font.italic: true
      }
      Repeater {
        // Collapsed: only the exercises you wrote about or skipped.
        model: entryBox.entry ? entryBox.entry.exercises.filter(function(e) { return entryBox.open || e.note || e.status === "skipped" }) : []
        delegate: Text {
          required property var modelData
          width: entryCol.width
          wrapMode: Text.WordWrap
          textFormat: Text.StyledText
          text: "•  " + root.exerciseLine(modelData)
          color: modelData.status === "skipped" ? root.muted : root.fg
          font.pixelSize: 14
        }
      }
      Repeater {
        model: entryBox.entry ? entryBox.entry.changes : []
        delegate: Text {
          required property var modelData
          width: entryCol.width
          wrapMode: Text.WordWrap
          textFormat: Text.StyledText
          text: (modelData.from !== modelData.to ? "↗  <b>" + root.esc(modelData.from) + " → " + root.esc(modelData.to) + "</b>"
                                                 : "↗  <b>" + root.esc(modelData.to) + "</b>")
                + "  <font color='" + root.muted + "'>(" + modelData.by + ") " + root.esc(modelData.reason) + "</font>"
          color: root.accent
          font.pixelSize: 13
        }
      }
      Column {
        visible: entryBox.entry !== null && entryBox.entry.coach !== null
        width: parent.width
        spacing: 6
        topPadding: 4
        Text { text: "Coach"; color: root.accent; font.pixelSize: 12; font.capitalization: Font.AllUppercase; font.letterSpacing: 1 }
        Text {
          visible: entryBox.entry && entryBox.entry.coach && !!entryBox.entry.coach.last_session
          width: parent.width
          wrapMode: Text.WordWrap
          textFormat: Text.PlainText
          text: entryBox.entry && entryBox.entry.coach ? entryBox.entry.coach.last_session : ""
          color: root.fg
          font.pixelSize: 14
        }
        Repeater {
          model: entryBox.entry && entryBox.entry.coach ? entryBox.entry.coach.recommendations : []
          delegate: Text {
            required property var modelData
            width: entryCol.width
            wrapMode: Text.WordWrap
            textFormat: Text.StyledText
            text: "•  <b>" + root.esc(modelData.about) + ":</b> " + root.esc(modelData.advice)
            color: root.fg
            font.pixelSize: 14
          }
        }
      }
    }
  }

  // A headline number.
  component Tile: Rectangle {
    property string label: ""
    property string value: ""
    property string detail: ""
    width: 200
    height: 92
    radius: 8
    color: root.panel
    border.width: 1
    border.color: root.line
    Column {
      x: 14; y: 12
      width: parent.width - 28
      spacing: 3
      Text { text: parent.parent.label; color: root.muted; font.pixelSize: 12; font.capitalization: Font.AllUppercase; font.letterSpacing: 1 }
      Text { text: parent.parent.value; color: root.fg; font.pixelSize: 24; font.weight: Font.DemiBold }
      Text { width: parent.width; elide: Text.ElideRight; text: parent.parent.detail; color: root.muted; font.pixelSize: 12 }
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
      Chip { label: "Report"; on: root.tab === "report"; onClicked: { root.tab = "report"; root.loadReport() } }
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
    // -- report: dossier, stats, last session, where to improve, journal ----------------------
    Flickable {
      anchors.top: tabs.bottom
      anchors.topMargin: 4
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      visible: root.tab === "report"
      contentHeight: reportCol.implicitHeight + 64
      clip: true
      boundsBehavior: Flickable.StopAtBounds

      Column {
        id: reportCol
        readonly property var r: root.report
        readonly property var stats: r ? r.stats : null
        x: 40; y: 32
        width: parent.width - 80
        spacing: 28

        Column {
          width: parent.width
          spacing: 8
          Text { text: "Your report"; color: root.fg; font.pixelSize: 30; font.weight: Font.DemiBold }
          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            color: root.muted
            font.pixelSize: 15
            text: !reportCol.r ? "Loading…"
                  : reportCol.r.dossier ? "The coach's review was written " + root.dayText(reportCol.r.dossier.written_at)
                    + ". The numbers are always up to date; the coach rewrites its part after each session."
                  : "The numbers below are always up to date. The coach writes your dossier and advice after each session, or now if you ask."
          }
          Row {
            spacing: 12
            Chip {
              label: root.reportWriting ? "The coach is writing…" : "Ask the coach to update it"
              on: root.reportWriting
              onClicked: if (!root.reportWriting) root.writeReport()
            }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              visible: root.reportWriting || root.reportError.length > 0
              text: root.reportError.length > 0 ? root.reportError : "about a minute; this page updates by itself"
              color: root.reportError.length > 0 ? root.urgent : root.muted
              font.pixelSize: 13
            }
          }
        }

        // -- dossier ------------------------------------------------------------------------
        Section {
          title: "Your dossier"
          visible: reportCol.r !== null
          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            text: reportCol.r && reportCol.r.dossier ? reportCol.r.dossier.text : "Not written yet: ask the coach above."
            color: reportCol.r && reportCol.r.dossier ? root.fg : root.muted
            font.pixelSize: 16
            lineHeight: 1.15
          }
          Text {
            visible: reportCol.r && reportCol.r.profile
            width: parent.width
            wrapMode: Text.WordWrap
            textFormat: Text.StyledText
            readonly property var p: reportCol.r ? reportCol.r.profile : null
            text: !p ? "" : "<b>" + p.experience.charAt(0).toUpperCase() + p.experience.slice(1) + "</b>"
                  + (p.age ? " · " + p.age + " years old" : "") + (p.weight_kg ? " · " + p.weight_kg + " kg" : "")
                  + (p.years_training ? " · " + p.years_training + " years of training" : "")
                  + " · goals: " + root.esc(p.goals.join(", "))
                  + " · trains with: " + root.esc(p.equipment.map(function(e) { return (root.equipmentLabels[e] || e).toLowerCase() }).join(", "))
                  + (reportCol.stats && reportCol.stats.first_day ? " · training with sportlock since " + root.dayText(reportCol.stats.first_day) : "")
            color: root.fg
            font.pixelSize: 14
          }
          Text {
            visible: reportCol.r && reportCol.r.profile && !!reportCol.r.profile.injuries
            width: parent.width
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            text: reportCol.r && reportCol.r.profile ? "Working around: " + reportCol.r.profile.injuries.replace(/^- /gm, "").split("\n").join("; ") : ""
            color: root.muted
            font.pixelSize: 14
          }
        }

        Section {
          title: "Your level on each movement"
          hint: "Each movement is a ladder of exercises from easiest to hardest. The bar shows your step."
          visible: reportCol.r !== null && reportCol.r.ladders.length > 0
          Repeater {
            model: reportCol.r ? reportCol.r.ladders : []
            delegate: Row {
              required property var modelData
              spacing: 14
              Text { width: reportCol.width * 0.17; elide: Text.ElideRight; text: root.chainText(modelData.chain); color: root.muted; font.pixelSize: 14; anchors.verticalCenter: parent.verticalCenter }
              Text { width: reportCol.width * 0.3; elide: Text.ElideRight; text: modelData.name; color: root.fg; font.pixelSize: 15; anchors.verticalCenter: parent.verticalCenter }
              Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: reportCol.width * 0.15; height: 8; radius: 4
                color: root.line
                Rectangle { width: parent.width * modelData.step / modelData.steps; height: parent.height; radius: 4; color: root.accent }
              }
              Text {
                anchors.verticalCenter: parent.verticalCenter
                width: reportCol.width * 0.38 - 42
                elide: Text.ElideRight
                text: "step " + modelData.step + " of " + modelData.steps
                      + (modelData.moved > 0 ? "  ·  +" + modelData.moved + " since start" : modelData.moved < 0 ? "  ·  " + modelData.moved + " (easier)" : "  ·  starting step")
                color: modelData.moved > 0 ? root.accent : root.muted
                font.pixelSize: 13
              }
            }
          }
        }

        Section {
          title: "What the coach remembers about you"
          hint: "The coach's long-term notes (remove a wrong one in the Profile tab)."
          visible: reportCol.r !== null && reportCol.r.memory.length > 0
          property bool all: false
          Repeater {
            model: reportCol.r ? (parent.all ? reportCol.r.memory : reportCol.r.memory.slice(0, 5)) : []
            delegate: Text {
              required property var modelData
              width: reportCol.width
              wrapMode: Text.WordWrap
              textFormat: Text.StyledText
              text: "<font color='" + root.muted + "'>" + modelData.topic + "</font>   " + root.esc(modelData.note)
              color: root.fg
              font.pixelSize: 14
            }
          }
          Text {
            id: memoryToggle
            visible: reportCol.r !== null && reportCol.r.memory.length > 5
            text: parent.all ? "show fewer ▴" : "show all " + (reportCol.r ? reportCol.r.memory.length : 0) + " notes ▾"
            color: root.accent
            font.pixelSize: 13
            MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: memoryToggle.parent.all = !memoryToggle.parent.all }
          }
        }

        // -- stats --------------------------------------------------------------------------
        Section {
          title: "Your stats"
          visible: reportCol.stats !== null
          Flow {
            width: parent.width
            spacing: 10
            Tile { label: "Sessions"; value: reportCol.stats ? reportCol.stats.sessions : ""; detail: reportCol.stats ? reportCol.stats.hard_sessions + " hard · " + reportCol.stats.light_sessions + " light" : "" }
            Tile { label: "Time trained"; value: reportCol.stats ? root.hoursText(reportCol.stats.minutes) : ""; detail: reportCol.stats && reportCol.stats.sessions ? "about " + Math.round(reportCol.stats.minutes / reportCol.stats.sessions) + " min a session" : "" }
            Tile { label: "Last 7 days"; value: reportCol.stats ? reportCol.stats.last_7_days.sessions + " sessions" : ""; detail: reportCol.stats ? reportCol.stats.last_7_days.minutes + " min · 28 days: " + reportCol.stats.last_28_days.sessions + " sessions" : "" }
            Tile { label: "Effort"; value: reportCol.stats && reportCol.stats.average_effort !== null ? reportCol.stats.average_effort + " / 10" : "–"; detail: reportCol.stats && reportCol.stats.recent_effort !== null ? "last sessions: " + reportCol.stats.recent_effort + " / 10" : "" }
            Tile { label: "Locks kept"; value: reportCol.stats ? reportCol.stats.locks_kept_in_a_row + " in a row" : ""; detail: reportCol.stats ? reportCol.stats.locks.completed + " done · " + reportCol.stats.locks.override + " overridden · " + reportCol.stats.locks.expired + " ran out" : "" }
            Tile { label: "Exercises"; value: reportCol.stats ? reportCol.stats.exercises_done + " done" : ""; detail: reportCol.stats ? reportCol.stats.exercises_skipped + " skipped · " + reportCol.stats.exercises_swapped + " made easier" : "" }
          }
          Text { text: "Minutes per week"; color: root.muted; font.pixelSize: 13; topPadding: 6 }
          Row {
            id: weekBars
            readonly property int most: reportCol.stats ? Math.max(30, Math.max.apply(null, reportCol.stats.weeks.map(function(w) { return w.minutes }))) : 30
            spacing: 10
            height: 150
            Repeater {
              model: reportCol.stats ? reportCol.stats.weeks : []
              delegate: Column {
                required property var modelData
                width: Math.min(72, (reportCol.width - 70) / 8)
                spacing: 4
                anchors.bottom: parent.bottom
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.minutes ? modelData.minutes + "′" : ""; color: root.fg; font.pixelSize: 12 }
                Rectangle {
                  anchors.horizontalCenter: parent.horizontalCenter
                  width: Math.min(40, parent.width - 8)
                  height: Math.max(2, 96 * modelData.minutes / weekBars.most)
                  radius: 4
                  color: modelData.minutes ? root.accent : root.line
                }
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: Qt.formatDate(new Date(modelData.start + "T12:00:00"), "d MMM"); color: root.muted; font.pixelSize: 11 }
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.sessions ? modelData.sessions + "×" : ""; color: root.muted; font.pixelSize: 11 }
              }
            }
          }
          Text { text: "Personal bests (best single set)"; color: root.muted; font.pixelSize: 13; topPadding: 6; visible: reportCol.r && reportCol.r.bests.length > 0 }
          Flow {
            width: parent.width
            spacing: 8
            Repeater {
              model: reportCol.r ? reportCol.r.bests : []
              delegate: Rectangle {
                required property var modelData
                width: bestText.implicitWidth + 24
                height: 34
                radius: 17
                color: root.panel
                border.width: 1
                border.color: root.line
                Text {
                  id: bestText
                  anchors.centerIn: parent
                  textFormat: Text.StyledText
                  text: root.esc(modelData.name) + "  <b>" + (modelData.reps !== null ? modelData.reps + " reps" : Math.round(modelData.seconds) + " s") + "</b>"
                        + "  <font color='" + root.muted + "'>" + Qt.formatDate(new Date(modelData.day + "T12:00:00"), "d MMM") + "</font>"
                  color: root.fg
                  font.pixelSize: 13
                }
              }
            }
          }
        }

        // -- last session -------------------------------------------------------------------
        Section {
          title: "Your last session"
          visible: reportCol.r !== null && reportCol.r.last_session !== null
          JournalEntry { entry: reportCol.r ? reportCol.r.last_session : null; open: true }
        }

        // -- where to improve ---------------------------------------------------------------
        Section {
          title: "Where you can improve"
          visible: reportCol.r !== null && (reportCol.r.improve.coach.length + reportCol.r.improve.numbers.length) > 0
          Repeater {
            model: reportCol.r ? reportCol.r.improve.coach : []
            delegate: Text {
              required property var modelData
              width: reportCol.width
              wrapMode: Text.WordWrap
              textFormat: Text.StyledText
              text: "•  <b>" + root.esc(modelData.area) + ":</b> " + root.esc(modelData.advice)
              color: root.fg
              font.pixelSize: 15
            }
          }
          Text {
            visible: reportCol.r !== null && reportCol.r.improve.numbers.length > 0
            text: "From your numbers"
            color: root.muted
            font.pixelSize: 13
            topPadding: 4
          }
          Repeater {
            model: reportCol.r ? reportCol.r.improve.numbers : []
            delegate: Text {
              required property var modelData
              width: reportCol.width
              wrapMode: Text.WordWrap
              textFormat: Text.StyledText
              text: "•  <b>" + root.esc(modelData.area) + ":</b> " + root.esc(modelData.advice)
              color: root.fg
              font.pixelSize: 14
            }
          }
        }

        // -- journal ------------------------------------------------------------------------
        Section {
          title: "Journal"
          hint: "Every session, newest first: what you did, what you wrote, what changed, and what the coach answered."
          visible: reportCol.r !== null && reportCol.r.journal.length > 0
          Repeater {
            model: reportCol.r ? reportCol.r.journal : []
            delegate: JournalEntry { required property var modelData; entry: modelData; width: reportCol.width }
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

        Section {
          title: "Spotify account"
          hint: root.musicAccount && root.musicAccount.connected
                ? "Connected as " + root.musicAccount.name + ". Every playlist in your account (and Liked Songs) is listed in the lock screen's music panel. sportlock only reads your playlists."
                : "Connect your Spotify account to pick any of your playlists on the lock screen. Spotify asks for an app of your own (free, once, no secret):"

          Column {
            width: parent.width
            spacing: 10
            visible: root.musicAccount !== null && !root.musicAccount.connected
            Row {
              spacing: 10
              Text { anchors.verticalCenter: parent.verticalCenter; text: "1."; color: root.fg; font.pixelSize: 15 }
              Chip { label: "Open the Spotify dashboard"; onClicked: Qt.openUrlExternally("https://developer.spotify.com/dashboard") }
              Text { anchors.verticalCenter: parent.verticalCenter; text: "log in, then Create app (any name and description)."; color: root.fg; font.pixelSize: 15 }
            }
            Row {
              spacing: 10
              Text { anchors.verticalCenter: parent.verticalCenter; text: "2. Redirect URI:"; color: root.fg; font.pixelSize: 15 }
              Field {
                id: redirectField
                implicitWidth: 330
                text: root.musicAccount ? root.musicAccount.redirect_uri : ""
                input.readOnly: true
                input.selectByMouse: true
              }
              Chip {
                label: "Copy"
                onClicked: { redirectField.input.selectAll(); redirectField.input.copy(); redirectField.input.deselect() }
              }
            }
            Text { text: "3. Tick “Web API”, save, and paste the app's Client ID here:"; color: root.fg; font.pixelSize: 15 }
          }

          Row {
            spacing: 10
            Field {
              id: clientIdField
              implicitWidth: 360
              visible: root.musicAccount !== null && !root.musicAccount.connected
              placeholder: "Client ID (32 letters and digits)"
            }
            Chip {
              visible: root.musicAccount !== null && !root.musicAccount.connected
              label: root.musicConnecting ? "Waiting for the browser…" : "Connect"
              on: !root.musicConnecting && clientIdField.text.trim().length === 32
              onClicked: if (!root.musicConnecting) root.connectSpotify()
            }
            Chip {
              visible: root.musicAccount !== null && root.musicAccount.connected
              label: "Disconnect"
              onClicked: root.disconnectSpotify()
            }
          }
          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            visible: text.length > 0
            text: root.musicError ? root.musicError
                  : root.musicConnecting ? "Allow sportlock in the browser window that just opened." : ""
            color: root.musicError ? root.urgent : root.muted
            font.pixelSize: 14
          }
        }

        Section {
          title: "Favourite music"
          hint: "Playlists, albums or songs shown first (★) in the lock screen's music panel; they work without connecting an account. In Spotify: Share → Copy link, then paste it here."
          Repeater {
            model: root.settings ? root.settings.music_playlists : []
            delegate: Row {
              id: playlistRow
              required property var modelData
              required property int index
              spacing: 8
              Field {
                implicitWidth: 220
                placeholder: "name"
                text: playlistRow.modelData.name
                onEdited: function(value) { root.settings.music_playlists[playlistRow.index].name = value }
              }
              Field {
                implicitWidth: Math.max(260, settingsForm.width - 220 - 120 - 16)
                placeholder: "https://open.spotify.com/playlist/…"
                text: playlistRow.modelData.link
                onEdited: function(value) { root.settings.music_playlists[playlistRow.index].link = value }
              }
              Chip { label: "Remove"; onClicked: root.removePlaylist(playlistRow.index) }
            }
          }
          Chip { label: "+ Add playlist"; onClicked: root.addPlaylist() }
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
