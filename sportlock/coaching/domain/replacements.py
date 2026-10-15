"""Exercises replaced after a session, and the athlete's answer to each.

After a session, the progression rules (and the coach's overrides) can move a ladder to another
exercise: bodyweight squats become step-ups. Before the next session, the athlete can agree, or
keep the old exercise; the coach reads the answers on its next run.
"""

from __future__ import annotations

from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.errors import DomainError
from sportlock.shared_kernel.targets import Target

MAX_NOTE_LENGTH = 300


class ReplacementError(DomainError):
    """There is no replacement to answer."""


class Replacement(ValueObject):
    """An exercise the last session's progression replaced with another, on one chain."""

    session_id: int
    chain: str
    from_exercise: str
    from_name: str
    from_target: Target  # what the replaced exercise would have been next time
    to_exercise: str
    to_name: str
    reason: str
    decided_by: str  # rules | agent

    def to_dict(self) -> dict:
        """The shape the screens show (part of the state-file and popup contracts)."""
        return {
            "session": self.session_id,
            "chain": self.chain,
            "from": self.from_exercise,
            "from_name": self.from_name,
            "to": self.to_exercise,
            "to_name": self.to_name,
            "reason": self.reason,
            "by": "coach" if self.decided_by == "agent" else "rules",
        }


class ReplacementAnswer(ValueObject):
    """What the athlete said about a replacement."""

    session_id: int
    chain: str
    from_exercise: str
    to_exercise: str
    agreed: bool
    note: str = ""
    answered_at: str

    @classmethod
    def to(cls, replacement: Replacement, *, agreed: bool, note: str, answered_at: str) -> ReplacementAnswer:
        """The answer to one replacement (the note is trimmed to one short paragraph)."""
        return cls(
            session_id=replacement.session_id,
            chain=replacement.chain,
            from_exercise=replacement.from_exercise,
            to_exercise=replacement.to_exercise,
            agreed=agreed,
            note=" ".join(note.split())[:MAX_NOTE_LENGTH],
            answered_at=answered_at,
        )


def replacements_after(
    session_id: int, moves: list[dict], current: dict[str, str], catalogue: Catalogue
) -> list[Replacement]:
    """The chains whose exercise the moves recorded for a session changed, and that are still on the new exercise.

    `moves` are the ladder repository's dicts in order; `current` is the exercise each chain is on now. The reason is
    that of the move that brought the new exercise in.
    """
    replacements = []
    for chain in dict.fromkeys(m["chain"] for m in moves):
        on_chain = [m for m in moves if m["chain"] == chain]
        before, now = on_chain[0], current.get(chain)
        if now is None or now == before["from"]:
            continue
        bringing = next((m for m in on_chain if m["to"] == now and m["from"] != now), None)
        target = _target(before.get("from_target") or {})
        if bringing is None or target is None or catalogue.find(before["from"]) is None or catalogue.find(now) is None:
            continue
        replacements.append(
            Replacement(
                session_id=session_id,
                chain=chain,
                from_exercise=before["from"],
                from_name=catalogue.name(before["from"]),
                from_target=target,
                to_exercise=now,
                to_name=catalogue.name(now),
                reason=bringing["reason"],
                decided_by=bringing.get("decided_by", "rules"),
            )
        )
    return replacements


def _target(data: dict) -> Target | None:
    try:
        return Target.from_dict(data)
    except KeyError, ValueError:
        return None
