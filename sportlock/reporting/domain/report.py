"""The trainee's report: numbers computed from every session (volume, effort, consistency, personal
bests, ladder levels) and plain observations drawn from them. The coach's own words come from its
review (coaching); this module only counts."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sportlock.shared_kernel.base import ValueObject

WEEKS_SHOWN = 8
RECENT_SESSIONS = 5
LOW_EFFORT = 3.5
SKIP_REASONS_SHOWN = 5
LAGGING_AFTER_SESSIONS = 6


class SetRecord(ValueObject):
    """One logged set."""

    reps: int | None = None
    seconds: float | None = None
    load_kg: float | None = None


class ExerciseRecord(ValueObject):
    """One exercise of a past session."""

    exercise: str
    name: str
    kind: str  # reps | hold | timed
    status: str  # done | skipped | swapped | pending
    rpe: int | None = None
    note: str = ""
    skip_reason: str = ""
    sets: tuple[SetRecord, ...] = ()


class SessionRecord(ValueObject):
    """One past session (tests and placeholders excluded)."""

    id: int
    day: date
    started_at: datetime | None
    finished_at: datetime | None
    kind: str  # scheduled | manual | outside
    status: str  # finished | abandoned | overridden
    title: str = ""
    day_type: str = ""
    rpe: int | None = None
    notes: str = ""
    exercises: tuple[ExerciseRecord, ...] = ()

    @property
    def minutes(self) -> int:
        """How long it lasted."""
        if not self.started_at or not self.finished_at:
            return 0
        return max(0, round((self.finished_at - self.started_at).total_seconds() / 60))

    @property
    def counts(self) -> bool:
        """Whether it counts as training (finished)."""
        return self.status == "finished"


class Window(ValueObject):
    """Sessions and minutes over a stretch of days."""

    sessions: int
    minutes: int


class Week(ValueObject):
    """One calendar week (Monday first)."""

    start: date
    sessions: int
    minutes: int


class TrainingStats(ValueObject):
    """The headline numbers."""

    sessions: int
    minutes: int
    first_day: date | None
    last_7_days: Window
    last_28_days: Window
    weeks: tuple[Week, ...]
    hard_sessions: int
    light_sessions: int
    average_effort: float | None
    recent_effort: float | None  # over the last few sessions
    exercises_done: int
    exercises_skipped: int
    exercises_swapped: int
    skip_reasons: tuple[str, ...]  # the latest different reasons
    locks: dict[str, int]  # scheduled lock outcomes: completed, override, expired, rest
    locks_kept_in_a_row: int


class PersonalBest(ValueObject):
    """The best single set on an exercise."""

    exercise: str
    name: str
    reps: int | None = None
    seconds: float | None = None
    day: date


class LadderLevel(ValueObject):
    """Where the trainee is on one progression chain."""

    chain: str
    exercise: str
    name: str
    step: int
    steps: int
    start_step: int

    @property
    def moved(self) -> int:
        """Steps climbed (negative: went down) since the starting point."""
        return self.step - self.start_step


class Observation(ValueObject):
    """Something the numbers show that is worth working on."""

    area: str
    advice: str


def _average(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _window(sessions: list[SessionRecord], since: date) -> Window:
    inside = [s for s in sessions if s.day >= since]
    return Window(sessions=len(inside), minutes=sum(s.minutes for s in inside))


def training_stats(sessions: list[SessionRecord], lock_outcomes: list[str | None], today: date) -> TrainingStats:
    """The numbers over every session; `lock_outcomes` are the scheduled locks' outcomes, oldest first."""
    done = sorted((s for s in sessions if s.counts), key=lambda s: (s.day, s.id))
    monday = today - timedelta(days=today.weekday())
    weeks = []
    for back in range(WEEKS_SHOWN - 1, -1, -1):
        start = monday - timedelta(weeks=back)
        inside = [s for s in done if start <= s.day < start + timedelta(days=7)]
        weeks.append(Week(start=start, sessions=len(inside), minutes=sum(s.minutes for s in inside)))
    exercises = [e for s in sessions for e in s.exercises]
    skipped = [(s.day, e.skip_reason) for s in sessions for e in s.exercises if e.status == "skipped"]
    outcomes = [o for o in lock_outcomes if o is not None]
    kept = 0
    for outcome in reversed(outcomes):
        if outcome == "rest":
            continue
        if outcome != "completed":
            break
        kept += 1
    efforts = [s.rpe for s in done if s.rpe]
    return TrainingStats(
        sessions=len(done),
        minutes=sum(s.minutes for s in done),
        first_day=done[0].day if done else None,
        last_7_days=_window(done, today - timedelta(days=6)),
        last_28_days=_window(done, today - timedelta(days=27)),
        weeks=tuple(weeks),
        hard_sessions=sum(1 for s in done if s.day_type == "hard"),
        light_sessions=sum(1 for s in done if s.day_type in ("light", "mobility")),
        average_effort=_average(efforts),
        recent_effort=_average(efforts[-RECENT_SESSIONS:]),
        exercises_done=sum(1 for e in exercises if e.status == "done"),
        exercises_skipped=len(skipped),
        exercises_swapped=sum(1 for e in exercises if e.status == "swapped"),
        skip_reasons=tuple(dict.fromkeys(reason for _, reason in sorted(skipped) if reason))[-SKIP_REASONS_SHOWN:],
        locks={o: outcomes.count(o) for o in ("completed", "override", "expired", "rest")},
        locks_kept_in_a_row=kept,
    )


def personal_bests(sessions: list[SessionRecord], exercises: set[str]) -> list[PersonalBest]:
    """The best set on each of `exercises` (the laddered ones), most recently set first. Reps
    exercises count reps, holds count seconds; a tie keeps the first time it was reached."""
    best: dict[str, PersonalBest] = {}
    for session in sorted(sessions, key=lambda s: (s.day, s.id)):
        for e in session.exercises:
            if e.exercise not in exercises or e.kind == "timed":
                continue
            for logged in e.sets:
                value = logged.reps if e.kind == "reps" else logged.seconds
                if not value:
                    continue
                current = best.get(e.exercise)
                if current is None or value > (current.reps if e.kind == "reps" else current.seconds or 0):
                    best[e.exercise] = PersonalBest(
                        exercise=e.exercise,
                        name=e.name,
                        reps=logged.reps if e.kind == "reps" else None,
                        seconds=round(logged.seconds, 1) if e.kind != "reps" and logged.seconds else None,
                        day=session.day,
                    )
    return sorted(best.values(), key=lambda b: b.day, reverse=True)


def observations(stats: TrainingStats, levels: list[LadderLevel]) -> list[Observation]:
    """Plain things the numbers show, most important first."""
    found = []
    if stats.recent_effort is not None and stats.sessions >= 3 and stats.recent_effort <= LOW_EFFORT:
        found.append(
            Observation(
                area="Effort",
                advice=f"Your last sessions averaged {stats.recent_effort:g}/10 effort. Strength and fitness "
                "come from sets that feel 6–8/10 near the end: slow the reps down and finish every set "
                "close to what you can do, and the ladders will move you up faster.",
            )
        )
    missed = stats.locks.get("override", 0) + stats.locks.get("expired", 0)
    if missed:
        found.append(
            Observation(
                area="Consistency",
                advice=f"{missed} scheduled lock{'s' if missed > 1 else ''} ended without a finished session "
                f"({stats.locks.get('override', 0)} overridden, {stats.locks.get('expired', 0)} ran out of time). "
                f"You've kept the last {stats.locks_kept_in_a_row} in a row; keep the streak going.",
            )
        )
    if stats.exercises_skipped >= 2:
        reasons = "; ".join(stats.skip_reasons[-3:])
        found.append(
            Observation(
                area="Skipped exercises",
                advice=f"You skipped {stats.exercises_skipped} exercises"
                + (f" (latest reasons: {reasons})" if reasons else "")
                + ". Tell the coach in the notes what got in the way so it can swap them for something that works.",
            )
        )
    if stats.sessions >= LAGGING_AFTER_SESSIONS:
        still = [level.name for level in levels if level.moved == 0]
        if still:
            found.append(
                Observation(
                    area="Patterns that haven't moved",
                    advice=f"Still on your starting step: {', '.join(still)}. Some are accessories the plans "
                    "rarely include; for the main patterns, hitting the top of the rep range at a moderate "
                    "effort is what moves you up.",
                )
            )
    return found
