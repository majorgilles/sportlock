from sportlock.progression.domain.rules import SetResult, propose
from sportlock.shared_kernel.targets import Target

EASIER = ("wall-push-up", "Wall push-up")
HARDER = ("knee-push-up", "Knee push-up")
REPS = Target(sets=3, rest=60, reps=(8, 12))
HOLD = Target(sets=3, rest=45, seconds=30)


def _reps(*values):
    return [SetResult(reps=v, seconds=30) for v in values]


def _holds(*values):
    return [SetResult(reps=None, seconds=v) for v in values]


def _propose(target, sets, rpe, kind="reps", easier=EASIER, harder=HARDER):
    return propose(
        exercise="incline-push-up",
        name="Incline push-up",
        kind=kind,
        target=target,
        sets=sets,
        rpe=rpe,
        easier=easier,
        harder=harder,
    )


def test_propose__easy_top_of_range__moves_up():
    # when
    proposal = _propose(REPS, _reps(12, 12, 13), 5)
    # then
    assert (proposal.rule, proposal.exercise, proposal.target.reps) == ("up", "knee-push-up", (8, 12))


def test_propose__moderate_top_of_range__adds_a_rep():
    proposal = _propose(REPS, _reps(12, 12, 12), 7)
    assert (proposal.rule, proposal.exercise, proposal.target.reps) == ("add", "incline-push-up", (9, 13))


def test_propose__moderate_at_twenty__moves_up_and_resets_the_range():
    proposal = _propose(REPS.with_(reps=(16, 20)), _reps(20, 20, 20), 8)
    assert (proposal.rule, proposal.exercise, proposal.target.reps) == ("up", "knee-push-up", (8, 12))


def test_propose__very_hard_but_done__holds():
    assert _propose(REPS, _reps(12, 12, 12), 9).rule == "hold"


def test_propose__inside_the_range__holds():
    proposal = _propose(REPS, _reps(11, 10, 9), 7)
    assert (proposal.rule, proposal.exercise) == ("hold", "incline-push-up")


def test_propose__below_the_range__moves_down():
    proposal = _propose(REPS, _reps(10, 8, 6), 7)
    assert (proposal.rule, proposal.exercise) == ("down", "wall-push-up")


def test_propose__fewer_sets_than_planned__moves_down():
    assert _propose(REPS, _reps(10, 9), 6).rule == "down"


def test_propose__grinding__moves_down():
    assert _propose(REPS, _reps(10, 9, 9), 9).rule == "down"


def test_propose__easiest_variation_too_hard__reduces_reps():
    proposal = _propose(REPS, _reps(5, 5, 4), 9, easier=None)
    assert (proposal.rule, proposal.exercise, proposal.target.reps) == ("down", "incline-push-up", (6, 10))


def test_propose__hardest_variation_easy__adds_two_reps():
    proposal = _propose(REPS, _reps(12, 12, 12), 5, harder=None)
    assert (proposal.rule, proposal.target.reps) == ("add", (10, 14))


def test_propose__no_rating__no_proposal():
    assert _propose(REPS, _reps(12, 12, 12), None) is None


def test_propose__easy_hold__adds_ten_seconds():
    proposal = _propose(HOLD, _holds(31, 30, 32), 5, kind="hold")
    assert (proposal.rule, proposal.target.seconds) == ("add", 40)


def test_propose__moderate_hold__adds_five_seconds():
    assert _propose(HOLD, _holds(30, 30, 30), 7, kind="hold").target.seconds == 35


def test_propose__easy_sixty_second_hold__moves_up_restarting_at_twenty():
    proposal = _propose(HOLD.with_(seconds=60), _holds(60, 61, 60), 6, kind="hold")
    assert (proposal.rule, proposal.exercise, proposal.target.seconds) == ("up", "knee-push-up", 20)


def test_propose__short_holds__move_down():
    proposal = _propose(HOLD, _holds(30, 20, 18), 8, kind="hold")
    assert (proposal.rule, proposal.exercise) == ("down", "wall-push-up")


def test_propose__timed_block__never_changes():
    assert _propose(Target(sets=1, rest=0, seconds=300), _holds(300), 3, kind="timed") is None


def test_propose__target_with_sides_and_progress__next_target_drops_them():
    # given
    target = REPS.with_(sides="each", progress="why")
    # when
    proposal = _propose(target, _reps(11, 10, 9), 7)
    # then
    assert (proposal.target.sides, proposal.target.progress) == (None, None)
