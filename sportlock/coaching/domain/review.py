"""The coach's review of the trainee, written after a session for the Report tab: who they are as a
trainee (the dossier), how the last session went, and where they can improve. Kept per session, so
the journal keeps every past answer."""

from __future__ import annotations

from sportlock.coaching.domain.plan import CoachOutputError, Recommendation
from sportlock.shared_kernel.base import ValueObject

MAX_IMPROVEMENTS = 4
MAX_TEXT = 1500


class Improvement(ValueObject):
    """One place the trainee can improve, and how."""

    area: str
    advice: str


class CoachReview(ValueObject):
    """What the coach wrote about one session and the trainee at that point."""

    session_id: int
    written_at: str
    dossier: str = ""
    last_session: str = ""
    improve: tuple[Improvement, ...] = ()
    recommendations: tuple[Recommendation, ...] = ()  # the answers to the session's feedback, from the plan

    def with_recommendations(self, recommendations: tuple[Recommendation, ...]) -> CoachReview:
        """The same review with the plan's answers to the session's feedback."""
        return self.model_copy(update={"recommendations": recommendations})


def parse_review(output: dict, *, session_id: int, written_at: str) -> CoachReview:
    """Validate the coach's review answer. Raises CoachOutputError."""

    def text(value: object) -> str:
        return str(value or "").strip()[:MAX_TEXT]

    dossier, last = text(output.get("dossier")), text(output.get("last_session"))
    if not dossier or not last:
        raise CoachOutputError("the review needs a dossier and a word on the last session")
    improve = [
        Improvement(area=text(i.get("area")), advice=text(i.get("advice")))
        for i in output.get("improve") or []
        if isinstance(i, dict)
    ]
    return CoachReview(
        session_id=session_id,
        written_at=written_at,
        dossier=dossier,
        last_session=last,
        improve=tuple(i for i in improve if i.area and i.advice)[:MAX_IMPROVEMENTS],
    )
