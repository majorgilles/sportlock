"""Whether the stored coach plan still matches the data: a new session or a profile change makes
it stale, and the next coach run replaces it."""

from __future__ import annotations

from sportlock.athlete.domain.profile import ProfileRepositoryProtocol
from sportlock.coaching.domain.plan import CoachPlan
from sportlock.coaching.domain.ports import CoachPlanRepositoryProtocol
from sportlock.training.domain.repositories import TrainingHistoryProtocol


class CoachPlanFreshness:
    """Computes the basis a plan must have been written from, and returns the plan only if it was."""

    def __init__(self, plans: CoachPlanRepositoryProtocol, history: TrainingHistoryProtocol,
                 profiles: ProfileRepositoryProtocol) -> None:
        self.plans = plans
        self.history = history
        self.profiles = profiles

    def basis(self) -> str:
        """Identifies the data a plan is written from: the latest counted session and the profile."""
        profile = self.profiles.get()
        return f"session:{self.history.last_counted_id() or 0}/profile:{profile.updated_at if profile else '-'}"

    def fresh_plan(self) -> CoachPlan | None:
        """The stored plan if it is up to date, else None."""
        plan = self.plans.get()
        return plan if plan and plan.basis == self.basis() else None

    def needs_run(self) -> bool:
        """A profile exists and there is no up-to-date plan."""
        return self.profiles.get() is not None and self.fresh_plan() is None
